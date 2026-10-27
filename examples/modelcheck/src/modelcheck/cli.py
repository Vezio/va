"""Command-line interface for modelcheck, which checks a serving stack layer by layer.

Commands:

    modelcheck profile validate PROFILE   Validate a profile offline and report each problem
                                          with its path.
    modelcheck profile show PROFILE       Show which cases would run or skip on each layer.
    modelcheck run --profile PROFILE      Run preflight checks and cases, and write the run
                                          directory.
    modelcheck job --profile PROFILE      Run unattended: run, compare, publish and notify.
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
    LayerKinds,
    LayerLocator,
    RunDir,
    RunOptions,
    Session,
    UsageError,
    add_run_arguments,
    emit,
    preflight,
    run_app,
)

from modelcheck import __version__
from modelcheck.cases import REGISTRY
from modelcheck.client import RecordingClient
from modelcheck.layers import KINDS
from modelcheck.profile import Profile, Target, load

PROG = "modelcheck"


class State:
    """State shared by all cases: the profile and one recording client per layer.

    Client creation is guarded by a lock, so the state can be shared by parallel workers.

    Args:
        profile: The loaded profile.
        run: The run directory that clients record exchanges into.
    """

    def __init__(self, profile: Profile, run: RunDir):
        self.profile = profile
        self.run = run
        self._clients: dict[str, RecordingClient] = {}
        self._lock = threading.Lock()

    def client(self, target: Target) -> RecordingClient:
        """Return the recording client for a target, creating it on first use.

        Args:
            target: The layer to return a client for.

        Returns:
            The client bound to the target, its API key and its kind's adapter.
        """
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
    """Build the checks that must pass before any case runs.

    The checks confirm that every referenced environment variable is set, that every layer
    answers on its health endpoint, and that the results destination is writable.

    Args:
        profile: The loaded profile.
        options: The run options, which provide the results destination.

    Returns:
        The preflight checks, in the order they run.
    """
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
    """The `run` command, which checks every layer that a profile declares."""

    name, help, passthrough = "run", "check every layer a profile declares", False

    def add_arguments(self, parser: argparse.ArgumentParser, app: App) -> None:
        """Add the `--profile` option and the shared run options to the parser.

        Args:
            parser: The command's argument parser.
            app: The application, which provides settings and their environment names.
        """
        parser.add_argument(
            "--profile",
            type=Path,
            default=app.settings.get(Key.PROFILE),
            help=f"the profile file (env {app.settings.name(Key.PROFILE)})",
        )
        add_run_arguments(parser, app)

    def run(self, args: argparse.Namespace, app: App) -> int:
        """Load the profile and execute a session against its targets.

        Args:
            args: The parsed command-line arguments.
            app: The application.

        Returns:
            The session's exit code.

        Raises:
            UsageError: If no profile is given on the command line or in the environment.
        """
        if not args.profile:
            raise UsageError(f"--profile (or {app.settings.name(Key.PROFILE)}) is required")
        profile = load(Path(args.profile))  # An invalid profile exits with status 2.
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
    """The `profile` command, which validates or shows a profile without network access."""

    name, help, passthrough = "profile", "validate or show a profile, offline", False

    def add_arguments(self, parser: argparse.ArgumentParser, app: App) -> None:
        """Add the action and profile path arguments to the parser.

        Args:
            parser: The command's argument parser.
            app: The application.
        """
        parser.add_argument(
            "action",
            choices=("validate", "show"),
            help="validate: report every problem; show: what would run or skip, per layer",
        )
        parser.add_argument("profile", type=Path, help="the profile file")

    def run(self, args: argparse.Namespace, app: App) -> int:
        """Validate the profile, or print the stack and each case's run or skip status per layer.

        Args:
            args: The parsed command-line arguments.
            app: The application.

        Returns:
            `ExitCode.OK` when the profile is valid.
        """
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
    """Describe each chain of layers in the profile, from the client inwards.

    Args:
        profile: The loaded profile.

    Returns:
        The chains separated by semicolons, for example `gateway (proxy) -> engine (engine)`.
    """
    inner = {t.behind for t in profile.targets if t.behind}
    chains = [
        " -> ".join(f"{t.name} ({t.kind})" for t in LayerKinds.chain(profile.targets, top.name))
        for top in profile.targets
        if top.name not in inner
    ]
    return "; ".join(chains)


def build_app() -> App:
    """Build the modelcheck application with its commands and the built-in commands.

    Returns:
        The configured application.
    """
    app = App(
        PROG,
        version=__version__,
        module="modelcheck",
        description="Check an OpenAI-compatible serving stack, layer by layer.",
    )
    return app.add(RunCommand()).add(ProfileCommand()).add_builtins()


def main() -> None:
    """Run the modelcheck command line and exit with its status."""
    run_app(build_app())
