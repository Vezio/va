"""`modelcheck`: check an OpenAI-compatible serving stack, layer by layer.

modelcheck profile validate PROFILE     offline: every problem, with its path
modelcheck profile show PROFILE         what would run and what would skip, per layer
modelcheck run --profile PROFILE        preflight, run, write the run directory
modelcheck job --profile PROFILE        unattended: run, compare, publish, notify
"""

from __future__ import annotations

import argparse
import threading
from pathlib import Path

from va import (
    App,
    ExitCode,
    LayerKinds,
    LayerLocator,
    RunOptions,
    Session,
    UsageError,
    add_run_arguments,
    preflight,
    run_app,
)
from va.log import emit
from va.redact import REDACTOR
from va.rundir import RunDir
from va.settings import Key

from . import __version__
from .cases import REGISTRY
from .client import RecordingClient
from .layers import KINDS
from .profile import Profile, Target, load

PROG = "modelcheck"


class State:
    """What every case shares: the profile and one recording client per layer (thread-safe,
    for --workers)."""

    def __init__(self, profile: Profile, run: RunDir):
        self.profile = profile
        self.run = run
        self._clients: dict[str, RecordingClient] = {}
        self._lock = threading.Lock()

    def client(self, target: Target) -> RecordingClient:
        with self._lock:
            if target.name not in self._clients:
                token = REDACTOR.secret_from_env(target.key_env) if target.key_env else None
                self._clients[target.name] = RecordingClient(
                    self.run,
                    target.base_url,
                    token=token,
                    timeout=30,
                    layer=target,
                    adapter=KINDS.adapter(target),
                )
            return self._clients[target.name]


def preflight_checks(profile: Profile, options: RunOptions) -> list[preflight.Check]:
    """Before any case: every variable set, every layer answering, the destination writable."""
    checks: list[preflight.Check] = []
    for t in profile.targets:
        checks.append(preflight.env_set(t.base_url_env, kind="base_url"))
        if t.key_env:
            checks.append(preflight.env_set(t.key_env))
        if t.base_url:
            checks.append(preflight.reachable(t.name, t.base_url + "/health"))
    if options.destination:
        checks.append(preflight.destination_writable(options.destination))
    return checks


class RunCommand:
    name, help, passthrough = "run", "check every layer a profile declares", False

    def add_arguments(self, parser: argparse.ArgumentParser, app: App) -> None:
        parser.add_argument(
            "--profile",
            type=Path,
            default=app.settings.get(Key.PROFILE),
            help=f"the profile file (env {app.settings.name(Key.PROFILE)})",
        )
        add_run_arguments(parser, app)

    def run(self, args: argparse.Namespace, app: App) -> int:
        if not args.profile:
            raise UsageError(f"--profile (or {app.settings.name(Key.PROFILE)}) is required")
        profile = load(Path(args.profile))  # an invalid profile exits 2
        options = RunOptions.from_args(args, app)
        return Session(
            tool=app.prog,
            run=options.run,
            cases=REGISTRY.select(options.selector),
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
            hooks=[*options.hooks(), LayerLocator(profile.targets)],
            workers=options.workers,
            case_timeout_s=options.case_timeout_s,
        ).execute()


class ProfileCommand:
    name, help, passthrough = "profile", "validate or show a profile, offline", False

    def add_arguments(self, parser: argparse.ArgumentParser, app: App) -> None:
        parser.add_argument(
            "action",
            choices=("validate", "show"),
            help="validate: report every problem; show: what would run or skip, per layer",
        )
        parser.add_argument("profile", type=Path, help="the profile file")

    def run(self, args: argparse.Namespace, app: App) -> int:
        profile = load(args.profile)
        if args.action == "validate":
            emit(
                f"{profile.name}: valid, {len(profile.targets)} target(s), digest {profile.digest}"
            )
            return ExitCode.OK
        emit("stack: " + _stack(profile))
        for t in profile.targets:
            caps = ", ".join(sorted(t.capabilities)) or "none"
            emit("")
            emit(f"{t.name}  ({t.kind}; ${t.base_url_env}; model {t.model}; capabilities: {caps})")
            for case in REGISTRY.select():
                missing = sorted(case.needs - t.capabilities)
                state = f"skip: undeclared ({', '.join(missing)})" if missing else "run"
                variants = "" if case.variants == ("",) else f" x{len(case.variants)}"
                emit(f"  {case.case_id:<30}{variants:<4} {state}")
        return ExitCode.OK


def _stack(profile: Profile) -> str:
    """Each chain of layers, from the client inwards: `gateway (proxy) -> engine (engine)`."""
    inner = {t.behind for t in profile.targets if t.behind}
    chains = [
        " -> ".join(f"{t.name} ({t.kind})" for t in LayerKinds.chain(profile.targets, top.name))
        for top in profile.targets
        if top.name not in inner
    ]
    return "; ".join(chains)


def build_app() -> App:
    app = App(
        PROG,
        version=__version__,
        module="modelcheck",
        description="Check an OpenAI-compatible serving stack, layer by layer.",
    )
    return app.add(RunCommand()).add(ProfileCommand()).add_builtins()


def main() -> None:
    run_app(build_app())
