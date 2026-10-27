"""The CLI skeleton: argparse, shared flags, one class per command, exit codes for blame.

argparse, not Click/Typer: those write help and errors straight to stdout/stderr, which fights
the single-logger contract, and `parse_known_args` is the plain way to forward unknown
arguments (to a child command, to an embedded test engine). `argparse.REMAINDER` is avoided:
it swallows later flags of our own.

Commands stay thin: parse, build a config, call a library function.

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
    name: str
    help: str
    # True when unknown arguments are forwarded to the command as `args.passthrough`.
    passthrough: bool

    def add_arguments(self, parser: argparse.ArgumentParser, app: App) -> None: ...

    def run(self, args: argparse.Namespace, app: App) -> int: ...


class UsageError(Exception):
    """Raise from a command for a usage problem the parser could not catch: exit 2."""


class App:
    """An app: its name, version, commands and the configuration they share."""

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
        self.module = module  # `python -m <module>` runs the app: how `job` starts its child
        self.skip_classes = skip_classes or SkipClasses.default()
        self.settings = settings or Settings.for_app(prog)
        self.commands: dict[str, Command] = {}
        self.logging: LogSetup | None = None  # set by main()

    def add(self, command: Command) -> App:
        if command.name in self.commands:
            raise ValueError(f"command {command.name!r} is defined twice")
        self.commands[command.name] = command
        return self

    def add_builtins(self) -> App:
        from .commands import builtin_commands  # noqa: PLC0415 -- commands imports this module

        for command in builtin_commands():
            self.add(command)
        return self

    def child_argv(self, command: str) -> list[str]:
        """How to start this app as a child process running `command`."""
        if self.module:
            return [sys.executable, "-m", self.module, command]
        return [sys.argv[0], command]

    def build_parser(self) -> argparse.ArgumentParser:
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
        # SIGTERM (a Job deadline, a CI cancel) behaves like Ctrl-C: keep what finished.
        previous = signal.signal(signal.SIGTERM, _interrupt)
        try:
            return int(command.run(args, self))
        except (UsageError, ProfileError) as exc:
            log.error("%s", exc)
            return ExitCode.USAGE
        except KeyboardInterrupt:
            log.warning("interrupted")
            return ExitCode.INTERRUPTED
        except Exception:  # noqa: BLE001 -- a defect in the tool, never a verdict
            log.exception("%s failed internally", self.prog)
            return ExitCode.INTERNAL
        finally:
            signal.signal(signal.SIGTERM, previous)


def _interrupt(signum, frame):  # noqa: ARG001
    raise KeyboardInterrupt


def run_app(app: App, argv: Sequence[str] | None = None) -> None:
    """Console-script entry point: `def main(): run_app(build_app())`."""
    sys.exit(app.main(argv))
