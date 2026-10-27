"""Command-line interface for httpcheck, an example application built on va.

This package is the template for new tools. The commands are:

    httpcheck profile validate PROFILE     Validate a profile offline and list each problem.
    httpcheck profile show PROFILE         Show which cases would run or skip, and why.
    httpcheck run --profile PROFILE        Run preflight, the cases, compare, publish, notify.
    httpcheck job --profile PROFILE        Run unattended in a child process, so that results
                                           are published even if the run crashes.
    httpcheck publish|compare|notify RUN   Run a built-in step over a finished run directory.
"""

from __future__ import annotations

import argparse
import threading
from pathlib import Path

from va import (
    REDACTOR,
    App,
    ExitCode,
    Key,
    RunDir,
    RunOptions,
    Session,
    UsageError,
    add_run_arguments,
    emit,
    preflight,
    run_app,
)

from httpcheck import __version__
from httpcheck.cases import REGISTRY
from httpcheck.client import RecordingClient
from httpcheck.profile import Profile, Target, load

PROG = "httpcheck"


class State:
    """State shared by all cases: the profile and one recording client per target.

    With `--workers` the state is shared across threads, so client creation is guarded by
    a lock.

    Args:
        profile: The loaded profile.
        run: Run directory that the clients record exchanges into.
    """

    def __init__(self, profile: Profile, run: RunDir):
        self.profile = profile
        self.run = run
        self._clients: dict[str, RecordingClient] = {}
        self._lock = threading.Lock()

    def client(self, target: Target) -> RecordingClient:
        """Return the recording client for a target, creating it on first use.

        The target's token is read with `REDACTOR.secret_from_env` so that it is registered
        for redaction before it is used.

        Args:
            target: The target to return a client for.

        Returns:
            The client for the target.
        """
        with self._lock:
            if target.name not in self._clients:
                token = REDACTOR.secret_from_env(target.key_env) if target.key_env else None
                self._clients[target.name] = RecordingClient(self.run, target.base_url, token=token)
            return self._clients[target.name]


def preflight_checks(profile: Profile, options: RunOptions) -> list[preflight.Check]:
    """Build the preflight checks that must pass before any case runs.

    Args:
        profile: The loaded profile.
        options: Parsed run options.

    Returns:
        Checks for required environment variables, health endpoint reachability and, when a
        destination is set, that the destination is writable.
    """
    checks: list[preflight.Check] = []
    for t in profile.targets:
        checks.append(preflight.env_set(t.base_url_env, kind="base_url"))
        if t.key_env:
            checks.append(preflight.env_set(t.key_env))
        if t.base_url and "health" in t.capabilities:
            checks.append(preflight.reachable(t.name, t.base_url + t.health_path))
    if options.destination:
        checks.append(preflight.destination_writable(options.destination))
    return checks


def _profile_arg(parser: argparse.ArgumentParser, app: App) -> None:
    """Add the `--profile` option, defaulting to the profile setting.

    Args:
        parser: Parser to add the option to.
        app: Application whose settings provide the default.
    """
    parser.add_argument(
        "--profile",
        type=Path,
        default=app.settings.get(Key.PROFILE),
        help=f"profile file (env {app.settings.name(Key.PROFILE)})",
    )


class RunCommand:
    """The `run` command, which checks the targets a profile declares."""

    name, help, passthrough = "run", "check the targets a profile declares", False

    def add_arguments(self, parser: argparse.ArgumentParser, app: App) -> None:
        """Add the profile option and the standard run options.

        Args:
            parser: Parser for this command.
            app: The application.
        """
        _profile_arg(parser, app)
        add_run_arguments(parser, app)

    def run(self, args: argparse.Namespace, app: App) -> int:
        """Load the profile, select the cases and execute a session.

        Args:
            args: Parsed command-line arguments.
            app: The application.

        Returns:
            The session exit code.

        Raises:
            UsageError: If no profile is given.
        """
        if not args.profile:
            raise UsageError("--profile (or HTTPCHECK_PROFILE) is required")
        profile = load(Path(args.profile))  # App.main maps ProfileError to exit code 2.
        cases = REGISTRY.select()
        options = RunOptions.from_args(args, app)
        return Session(
            tool=app.prog,
            run=options.run,
            cases=[c for c in cases if options.selector.matches(c)],
            targets=profile.targets,
            state=State(profile, options.run),
            subject={"name": profile.name, "digest": profile.digest},
            labels=options.labels,
            links=options.links,
            checks=preflight_checks(profile, options),
            destination=options.destination,
            group=options.group or profile.name,
            baseline=options.baseline,
            budget_s=options.budget_s,
            summary_path=options.summary,
            notifiers=options.notifiers,
            skip_classes=app.skip_classes,
            hooks=options.hooks(),
            workers=options.workers,
            case_timeout_s=options.case_timeout_s,
        ).execute()


class ProfileCommand:
    """The `profile` command, which validates or shows a profile without network access."""

    name, help, passthrough = "profile", "validate or show a profile, offline", False

    def add_arguments(self, parser: argparse.ArgumentParser, app: App) -> None:
        """Add the action and profile path arguments.

        Args:
            parser: Parser for this command.
            app: The application.
        """
        parser.add_argument(
            "action",
            choices=("validate", "show"),
            help="validate: report every problem; show: what would run or skip, per target",
        )
        parser.add_argument("profile", type=Path, help="the profile file")

    def run(self, args: argparse.Namespace, app: App) -> int:
        """Validate the profile, or list each target's cases and whether they would run.

        Args:
            args: Parsed command-line arguments.
            app: The application.

        Returns:
            `ExitCode.OK`. An invalid profile raises `ProfileError` from `load`.
        """
        profile = load(args.profile)
        if args.action == "validate":
            emit(
                f"{profile.name}: valid, {len(profile.targets)} target(s), digest {profile.digest}"
            )
            return ExitCode.OK
        for target in profile.targets:
            caps = ", ".join(sorted(target.capabilities)) or "none"
            emit(f"{target.name}  (${target.base_url_env}, capabilities: {caps})")
            for case in REGISTRY.select():
                missing = sorted(case.needs - target.capabilities)
                state = f"skip:undeclared ({', '.join(missing)})" if missing else "run"
                variants = "" if case.variants == ("",) else f" x{len(case.variants)}"
                tags = f" [{', '.join(sorted(case.tags))}]" if case.tags else ""
                emit(f"  {case.case_id:<28}{variants:<4} {state}{tags}")
        return ExitCode.OK


def build_app() -> App:
    """Build the httpcheck application with its commands and the va built-ins.

    Returns:
        The configured application.
    """
    app = App(
        PROG,
        version=__version__,
        module="httpcheck",
        description="Check deployed HTTP services against a profile.",
    )
    return app.add(RunCommand()).add(ProfileCommand()).add_builtins()


def main() -> None:
    """Run the httpcheck command line and exit with its status."""
    run_app(build_app())
