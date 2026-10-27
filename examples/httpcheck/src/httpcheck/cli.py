"""`httpcheck`: an example app built on cli_framework. Copy it to start a new tool.

httpcheck profile validate PROFILE     offline: every problem with its path
httpcheck profile show PROFILE         what would run, and what would skip and why
httpcheck run --profile PROFILE        preflight, run, compare, publish, notify
httpcheck job --profile PROFILE        unattended: the same, surviving a crashed run
httpcheck publish|compare|notify RUN   the built-ins, over a finished run directory
"""

from __future__ import annotations

import argparse
import threading
from pathlib import Path

from cli_framework import (
    App,
    ExitCode,
    RunOptions,
    Session,
    UsageError,
    add_run_arguments,
    preflight,
    run_app,
)
from cli_framework.log import emit
from cli_framework.redact import REDACTOR
from cli_framework.rundir import RunDir
from cli_framework.settings import Key

from . import __version__
from .cases import REGISTRY
from .client import RecordingClient
from .profile import Profile, Target, load

PROG = "httpcheck"


class State:
    """What every case shares: the profile and one recording client per target. Shared across
    worker threads with --workers, so creating a client is locked."""

    def __init__(self, profile: Profile, run: RunDir):
        self.profile = profile
        self.run = run
        self._clients: dict[str, RecordingClient] = {}
        self._lock = threading.Lock()

    def client(self, target: Target) -> RecordingClient:
        with self._lock:
            if target.name not in self._clients:
                token = REDACTOR.secret_from_env(target.key_env) if target.key_env else None
                self._clients[target.name] = RecordingClient(self.run, target.base_url, token=token)
            return self._clients[target.name]


def preflight_checks(profile: Profile, options: RunOptions) -> list[preflight.Check]:
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
    parser.add_argument(
        "--profile",
        type=Path,
        default=app.settings.get(Key.PROFILE),
        help=f"profile file (env {app.settings.name(Key.PROFILE)})",
    )


class RunCommand:
    name, help, passthrough = "run", "check the targets a profile declares", False

    def add_arguments(self, parser: argparse.ArgumentParser, app: App) -> None:
        _profile_arg(parser, app)
        add_run_arguments(parser, app)

    def run(self, args: argparse.Namespace, app: App) -> int:
        if not args.profile:
            raise UsageError("--profile (or HTTPCHECK_PROFILE) is required")
        profile = load(Path(args.profile))  # a ProfileError exits 2 through App.main
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
    name, help, passthrough = "profile", "validate or show a profile, offline", False

    def add_arguments(self, parser: argparse.ArgumentParser, app: App) -> None:
        parser.add_argument("action", choices=("validate", "show"))
        parser.add_argument("profile", type=Path)

    def run(self, args: argparse.Namespace, app: App) -> int:
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
    app = App(
        PROG,
        version=__version__,
        module="httpcheck",
        description="Check deployed HTTP services against a profile.",
    )
    return app.add(RunCommand()).add(ProfileCommand()).add_builtins()


def main() -> None:
    run_app(build_app())
