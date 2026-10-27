"""Command-line application skeleton built on argparse.

The skeleton provides shared flags, one class per command and exit codes that identify the
party responsible for a failure.

argparse is used instead of Click or Typer because those libraries write help and errors
directly to stdout and stderr, which conflicts with routing all output through the logger.
`parse_known_args` also provides a simple way to forward unknown arguments to a child command
or an embedded test engine. `argparse.REMAINDER` is not used, because it consumes any of the
application's own flags that follow it.

Commands are kept thin: they parse arguments, build a configuration and call a library
function.

Example:
    app = App("mytool", version="0.1.0", module="mytool")
    app.add(RunCommand()).add_builtins()
    raise SystemExit(app.main())
"""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Sequence
from typing import Protocol

from .exit_codes import ExitCode
from .log import LogSetup, configure, get_logger
from .model import SkipClasses
from .profile import ProfileError
from .settings import Settings


class Command(Protocol):
    """Contract for a subcommand registered with an `App`.

    A command may also define a `description` attribute, which is used as the subcommand's
    description in place of `help`.

    Attributes:
        name: Subcommand name on the command line.
        help: One-line help text shown in the command list.
        passthrough: True if unknown arguments are accepted and forwarded to the command as
            `args.passthrough`. If False, unknown arguments are a usage error.
    """

    name: str
    help: str
    passthrough: bool

    def add_arguments(self, parser: argparse.ArgumentParser, app: App) -> None:
        """Add the command's arguments to its subparser.

        Args:
            parser: Subparser for this command.
            app: Application the command belongs to.
        """
        ...

    def run(self, args: argparse.Namespace, app: App) -> int:
        """Execute the command and return its exit code.

        A command raises `UsageError` or `ProfileError` for a usage problem. Any other
        exception is reported as an internal error.

        Args:
            args: Parsed arguments, including `passthrough`.
            app: Application the command belongs to.

        Returns:
            The process exit code, normally an `ExitCode` value.
        """
        ...


class UsageError(Exception):
    """Usage problem detected by a command after argument parsing.

    `App.main` reports it and exits with the USAGE code (2).
    """


class App:
    """Command-line application with its name, version, commands and shared configuration.

    Args:
        prog: Program name, used in help output and to derive the settings prefix.
        version: Version string shown by `--version`.
        description: Description shown in the top-level help.
        module: Module name such that `python -m <module>` runs the application. The `job`
            command uses it to start its child process.
        skip_classes: Skip classes recognised by the application. Defaults to the standard set.
        settings: Environment settings. Defaults to settings derived from `prog`.

    Attributes:
        prog: Program name.
        version: Version string.
        description: Description shown in the top-level help.
        module: Module name used to start the application as a child process, or None.
        skip_classes: Skip classes recognised by the application.
        settings: Environment settings.
        commands: Registered commands by name.
        logging: Logging configuration, set by `main`.
    """

    def __init__(
        self,
        prog: str,
        *,
        version: str,
        description: str = "",
        module: str | None = None,
        skip_classes: SkipClasses | None = None,
        settings: Settings | None = None,
    ):
        self.prog = prog
        self.version = version
        self.description = description
        self.module = module
        self.skip_classes = skip_classes or SkipClasses.default()
        self.settings = settings or Settings.for_app(prog)
        self.commands: dict[str, Command] = {}
        self.logging: LogSetup | None = None

    def add(self, command: Command) -> App:
        """Register a command.

        Args:
            command: Command to register.

        Returns:
            This application, to allow chaining.

        Raises:
            ValueError: If a command with the same name is already registered.
        """
        if command.name in self.commands:
            raise ValueError(f"command {command.name!r} is defined twice")
        self.commands[command.name] = command
        return self

    def add_builtins(self) -> App:
        """Register the framework's built-in commands.

        Returns:
            This application, to allow chaining.
        """
        # Imported here because the commands module imports this module.
        from .commands import builtin_commands  # noqa: PLC0415

        for command in builtin_commands():
            self.add(command)
        return self

    def child_argv(self, command: str) -> list[str]:
        """Return the argument vector that starts this application running a command.

        Args:
            command: Name of the command the child process runs.

        Returns:
            `python -m <module> <command>` when a module is set, otherwise the current
            script followed by the command.
        """
        if self.module:
            return [sys.executable, "-m", self.module, command]
        return [sys.argv[0], command]

    def build_parser(self) -> argparse.ArgumentParser:
        """Build the argument parser with the shared logging flags and one subparser per command.

        Returns:
            The top-level parser.
        """
        shared = argparse.ArgumentParser(add_help=False)
        group = shared.add_argument_group("logging")
        group.add_argument(
            "--log-level",
            default="INFO",
            choices=["DEBUG", "INFO", "WARNING", "ERROR"],
            help="diagnostics on stderr at this level and above (default INFO); "
            "the run's own run.log always has DEBUG",
        )
        group.add_argument("-q", "--quiet", action="store_true", help="warnings and errors only")
        group.add_argument(
            "--log-format",
            default="text",
            choices=["text", "json"],
            help="json writes one object per diagnostic line, for log collectors",
        )
        group.add_argument("--no-color", action="store_true", help="never emit colour")
        parser = argparse.ArgumentParser(
            prog=self.prog,
            description=self.description,
            epilog=ExitCode.epilog(),
            parents=[shared],
            allow_abbrev=False,
        )
        parser.add_argument("--version", action="version", version=f"{self.prog} {self.version}")
        sub = parser.add_subparsers(dest="command", required=True)
        for command in self.commands.values():
            child = sub.add_parser(
                command.name,
                help=command.help,
                description=getattr(command, "description", None) or command.help,
                parents=[shared],
                allow_abbrev=False,
                epilog=ExitCode.epilog(),
            )
            command.add_arguments(child, self)
            child.set_defaults(_command=command)
        return parser

    def main(self, argv: Sequence[str] | None = None) -> int:
        """Parse arguments, configure logging and run the selected command.

        SIGTERM is handled like an interrupt while the command runs. Usage and profile errors
        map to USAGE, interrupts to INTERRUPTED and any other exception to INTERNAL.

        Args:
            argv: Arguments to parse. Defaults to `sys.argv[1:]`.

        Returns:
            The process exit code.
        """
        parser = self.build_parser()
        args, unknown = parser.parse_known_args(argv)
        command: Command = args._command
        if unknown and not command.passthrough:
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")  # exits 2
        args.passthrough = unknown
        self.logging = configure(
            app=self.prog,
            level="WARNING" if args.quiet else args.log_level,
            fmt=args.log_format,
            no_color=args.no_color,
        )
        log = get_logger(f"{self.prog}.cli")
        # SIGTERM, as sent for a Job deadline or a CI cancellation, is handled like Ctrl-C so
        # that the results of finished cases are kept.
        previous = signal.signal(signal.SIGTERM, _interrupt)
        try:
            return int(command.run(args, self))
        except (UsageError, ProfileError) as exc:
            log.error("%s", exc)
            return ExitCode.USAGE
        except KeyboardInterrupt:
            log.warning("interrupted")
            return ExitCode.INTERRUPTED
        except Exception:  # noqa: BLE001
            # An unexpected exception is a defect in the tool.
            log.exception("%s failed internally", self.prog)
            return ExitCode.INTERNAL
        finally:
            signal.signal(signal.SIGTERM, previous)


def _interrupt(signum, frame):  # noqa: ARG001
    """Raise KeyboardInterrupt from a signal handler.

    Args:
        signum: Signal number.
        frame: Current stack frame.

    Raises:
        KeyboardInterrupt: Always, so that the signal is handled like Ctrl-C.
    """
    raise KeyboardInterrupt


def run_app(app: App, argv: Sequence[str] | None = None) -> None:
    """Run an application and exit the process with its exit code.

    Intended for use in a console-script entry point, e.g. `def main(): run_app(build_app())`.

    Args:
        app: Application to run.
        argv: Arguments to parse. Defaults to `sys.argv[1:]`.
    """
    sys.exit(app.main(argv))
