"""Built-in commands, and the standard flags of an app's `run` command.

    publish  RUN_DIR    publish a finished run directory (manifest last)
    compare  RUN_DIR    compare a run with earlier published runs
    notify   RUN_DIR    send a run's report through any notifiers
    job                 one unattended run: `<app> run` as a child, then compare, publish
                        (even after a crash) and notify. What a CronJob or CI runs.

Every flag's default comes from `App.settings` (`<PREFIX>_<KEY>`), so a flag and its variable
are one option. An app's `run` command calls `add_run_arguments` and `RunOptions.from_args`.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .cli import App, Command, UsageError
from .compare import Comparison
from .defaults import DEFAULTS
from .exit_codes import ExitCode, combine
from .hooks import ProgressLog, RunHooks
from .layout import RunFile
from .log import emit, get_logger
from .model import RunOutcome
from .notifiers import Notifier, NotifierError, notify_all, parse_specs
from .outputs import HtmlReport, JUnitReport, MetricsFile
from .publish import deliver
from .report import RunReport
from .rundir import RunDir, new_run_id
from .runner import Selector
from .session import Baseline, compare_with_archive
from .settings import Key
from .sinks import UnsupportedDestination, sink_for

log = get_logger("commands")


class Arguments(Protocol):
    """An argparse parser or argument group: anything flags can be added to."""

    def add_argument(self, *args: Any, **kwargs: Any) -> Any: ...


def parse_labels(values: list[str]) -> dict[str, str]:
    labels = {}
    for item in values:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise UsageError(f"--label takes KEY=VALUE, not {item!r}")
        labels[key] = value
    return labels


def notifiers_from(specs: list[str]) -> list[Notifier]:
    try:
        return parse_specs(specs)
    except NotifierError as exc:
        raise UsageError(f"--notify: {exc}") from exc


def _float_setting(app: App, key: Key, default: float | None) -> float | None:
    try:
        return app.settings.get_float(key, default)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc


def add_destination_arguments(parser: Arguments, app: App, *, reading: bool = False) -> None:
    """--remote-output and --group. `reading` words them for commands that read the archive."""
    s = app.settings
    parser.add_argument(
        "--remote-output",
        metavar="URI",
        default=s.get(Key.RESULTS_URI),
        help=(
            "the archive of published runs to read"
            if reading
            else "publish the run here: file:///path, s3://bucket/prefix or a plugin scheme; "
            "unset keeps results local"
        )
        + f" (env {s.name(Key.RESULTS_URI)})",
    )
    parser.add_argument(
        "--group",
        metavar="NAME",
        default=s.get(Key.GROUP),
        help=f"archive partition: runs are compared within a group (env {s.name(Key.GROUP)})",
    )


def add_notify_arguments(parser: Arguments, app: App) -> None:
    s = app.settings
    parser.add_argument(
        "--notify",
        action="append",
        default=None,
        metavar="KIND:TARGET",
        help="send the report here (repeatable): slack:env:VAR, webhook:URL, exec:CMD, "
        f"file:PATH or log:. Replaces {s.name(Key.NOTIFY)} (comma-separated)",
    )


def notify_specs(args: argparse.Namespace, app: App) -> list[str]:
    """The notifier specs: --notify when given, else the environment's."""
    return list(args.notify) if args.notify else app.settings.get_list(Key.NOTIFY)


def add_baseline_arguments(parser: Arguments, app: App, *, switch: bool = True) -> None:
    """The comparison flags. `switch` adds --compare (for commands where comparing is optional)."""
    s = app.settings
    if switch:
        parser.add_argument(
            "--compare",
            action="store_true",
            help="compare with earlier published runs before publishing (needs --remote-output)",
        )
    parser.add_argument(
        "--compare-window",
        type=int,
        metavar="N",
        default=1,
        help="how many earlier runs must all have passed before a failure is a regression; "
        "when they disagree the case is reported as flaky (default 1)",
    )
    baseline_label = s.get(Key.BASELINE_LABEL, "trigger=nightly")
    parser.add_argument(
        "--baseline-label",
        metavar="KEY=VALUE",
        default=baseline_label,
        help=f"only runs carrying this label are baselines; empty for any run (default "
        f"{baseline_label}, env {s.name(Key.BASELINE_LABEL)})",
    )
    parser.add_argument(
        "--baseline",
        metavar="RUN_ID",
        default=None,
        help="compare with this one run instead",
    )


def baseline_from(args: argparse.Namespace) -> Baseline | None:
    if not args.compare:
        return None
    return Baseline(args.compare_window, args.baseline_label or None, args.baseline)


# ------------------------------------------------------------------ an app's run command


def add_run_arguments(parser: argparse.ArgumentParser, app: App) -> None:
    """The flags every app's `run` shares, so `job`, CI and people drive every app alike."""
    s = app.settings
    out = parser.add_argument_group("output")
    out.add_argument(
        "--work-dir",
        type=Path,
        metavar="DIR",
        default=Path(s.get(Key.WORK_DIR) or "runs"),
        help=f"where run directories are created (default runs, env {s.name(Key.WORK_DIR)})",
    )
    out.add_argument(
        "--run-dir",
        type=Path,
        metavar="DIR",
        default=None,
        help="create the run directory exactly here (`job` sets this)",
    )
    add_destination_arguments(out, app)
    out.add_argument(
        "--summary",
        type=Path,
        metavar="FILE",
        default=None,
        help="also append the Markdown summary to FILE, e.g. $GITHUB_STEP_SUMMARY",
    )
    out.add_argument(
        "--label",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="label the run (repeatable); trigger=nightly marks a baseline for comparisons",
    )
    out.add_argument(
        "--link",
        action="append",
        default=[],
        metavar="NAME=URL",
        help="show a link in the report and notifications, e.g. ci=https://... (repeatable)",
    )
    add_notify_arguments(out, app)
    sel = parser.add_argument_group("selection")
    sel.add_argument(
        "-k",
        dest="patterns",
        action="append",
        default=[],
        metavar="TEXT",
        help="run only cases whose id contains TEXT (repeatable: any may match)",
    )
    sel.add_argument(
        "--tag",
        dest="tags",
        action="append",
        default=[],
        metavar="TAG",
        help="run only cases with this tag (repeatable: all must match)",
    )
    sel.add_argument(
        "--exclude-tag",
        dest="exclude_tags",
        action="append",
        default=[],
        metavar="TAG",
        help="skip cases with this tag (repeatable)",
    )
    add_baseline_arguments(parser.add_argument_group("comparison"), app)
    add_execution_arguments(parser.add_argument_group("execution"), app)
    add_output_arguments(parser.add_argument_group("reports (opt-in)"), app)


def _int_setting(app: App, key: Key, default: int | None) -> int | None:
    try:
        return app.settings.get_int(key, default)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc


def add_execution_arguments(parser: Arguments, app: App) -> None:
    """How cases run. The defaults: one at a time, no timeout, no budget."""
    s = app.settings
    parser.add_argument(
        "--workers",
        type=int,
        metavar="N",
        default=None,
        help=f"opt-in: run cases on N threads; cases marked exclusive still run alone "
        f"(default 1, env {s.name(Key.WORKERS)})",
    )
    parser.add_argument(
        "--case-timeout",
        type=float,
        metavar="SECONDS",
        default=None,
        help=f"opt-in: abandon a case after SECONDS and record it as an error "
        f"(env {s.name(Key.CASE_TIMEOUT)})",
    )
    parser.add_argument(
        "--budget",
        type=float,
        metavar="SECONDS",
        default=None,
        help=f"start no new case after SECONDS; the run ends as interrupted "
        f"(env {s.name(Key.RUN_BUDGET)})",
    )


def add_output_arguments(parser: Arguments, app: App) -> None:
    """Opt-in run outputs, written into the run directory and published with it."""
    parser.add_argument(
        "--html",
        nargs="?",
        const=True,
        default=None,
        metavar="PATH",
        help="write report.html into the run (and a copy to PATH if given)",
    )
    parser.add_argument(
        "--junit",
        nargs="?",
        const=True,
        default=None,
        metavar="PATH",
        help="write junit.xml into the run, for CI (and a copy to PATH if given)",
    )
    parser.add_argument(
        "--metrics-file",
        type=Path,
        metavar="PATH",
        default=app.settings.get(Key.METRICS_FILE),
        help="write Prometheus metrics to PATH, atomically, e.g. for node_exporter's textfile "
        f"collector (env {app.settings.name(Key.METRICS_FILE)})",
    )


@dataclass(frozen=True)
class RunOptions:
    """The parsed standard run flags, typed."""

    run: RunDir
    destination: str | None
    group: str | None
    summary: Path | None
    labels: dict[str, str]
    links: dict[str, str]
    selector: Selector
    budget_s: float | None
    notifiers: list[Notifier] = field(default_factory=list)
    baseline: Baseline | None = None
    workers: int = 1
    case_timeout_s: float | None = None
    outputs: list[RunHooks] = field(default_factory=list)

    def hooks(self) -> list[RunHooks]:
        """The default progress log plus the outputs the flags asked for."""
        return [ProgressLog(), *self.outputs]

    @classmethod
    def from_args(cls, args: argparse.Namespace, app: App) -> RunOptions:
        """Validates everything before creating the run directory: a usage error leaves no
        directory behind."""
        labels = parse_labels(args.label)
        links = parse_labels(args.link)
        notifiers = notifiers_from(notify_specs(args, app))
        workers = args.workers if args.workers is not None else _int_setting(app, Key.WORKERS, 1)
        workers = 1 if workers is None else workers
        if workers < 1:
            raise UsageError("--workers must be at least 1")
        case_timeout = (
            args.case_timeout
            if args.case_timeout is not None
            else _float_setting(app, Key.CASE_TIMEOUT, None)
        )
        if case_timeout is not None and case_timeout <= 0:
            raise UsageError("--case-timeout must be positive")
        outputs: list[RunHooks] = []
        if args.html:
            outputs.append(HtmlReport(None if args.html is True else Path(args.html)))
        if args.junit:
            outputs.append(JUnitReport(None if args.junit is True else Path(args.junit)))
        if args.metrics_file:
            outputs.append(MetricsFile(Path(args.metrics_file), prefix=app.settings.prefix.lower()))
        if args.remote_output:
            try:
                sink_for(args.remote_output)
            except UnsupportedDestination as exc:
                raise UsageError(str(exc)) from exc
        if args.run_dir:
            run = RunDir.at(args.run_dir)
        else:
            run = RunDir.create(args.work_dir)
        if app.logging:
            app.logging.add_file(run.file("run.log"))  # redacted, published with the run
        return cls(
            run=run,
            destination=args.remote_output,
            group=args.group,
            summary=args.summary,
            labels=labels,
            links=links,
            selector=Selector(
                tuple(args.patterns), frozenset(args.tags), frozenset(args.exclude_tags)
            ),
            budget_s=args.budget or _float_setting(app, Key.RUN_BUDGET, None),
            notifiers=notifiers,
            baseline=baseline_from(args),
            workers=workers,
            case_timeout_s=case_timeout,
            outputs=outputs,
        )


# ------------------------------------------------------------------ built-ins


class PublishCommand:
    name, help, passthrough = "publish", "publish a finished run directory (manifest last)", False
    description = (
        "Publish a finished run directory to --remote-output, under <group>/<run id>/, with "
        "MANIFEST.json uploaded last. Records the result in the run's outcome.json."
    )

    def add_arguments(self, parser, app):
        parser.add_argument("run_dir", type=Path, help="the run directory to publish")
        add_destination_arguments(parser, app)

    def run(self, args, app) -> int:
        if not (args.remote_output and args.group):
            raise UsageError("publish needs --remote-output and --group (or their variables)")
        run = RunDir(args.run_dir.resolve())
        publication = deliver(run, args.remote_output, args.group)
        outcome = run.outcome()
        if outcome is not None:
            outcome.publication = publication
            run.save_outcome(outcome)
        emit(f"{publication.status}: {publication.location or publication.reason}")
        return ExitCode.OK if publication.delivered else ExitCode.NOT_DELIVERED


class CompareCommand:
    name, help, passthrough = "compare", "compare a run with earlier published runs", False
    description = (
        "Compare a run directory with earlier runs published in the same group, and write "
        "comparison.json and comparison.md into it."
    )

    def add_arguments(self, parser, app):
        parser.add_argument("run_dir", type=Path, help="the run directory to compare")
        add_destination_arguments(parser, app, reading=True)
        add_baseline_arguments(parser, app, switch=False)
        parser.add_argument(
            "--fail-on-regression",
            action="store_true",
            help="exit 1 when there is a regression (default: report it and exit 0)",
        )

    def run(self, args, app) -> int:
        if not (args.remote_output and args.group):
            raise UsageError("compare needs --remote-output and --group (or their variables)")
        baseline = Baseline(args.compare_window, args.baseline_label or None, args.baseline)
        result = compare_with_archive(
            RunDir(args.run_dir.resolve()), args.remote_output, args.group, baseline
        )
        emit(result.markdown())
        failed = args.fail_on_regression and result.regressed
        return ExitCode.FAILURES if failed else ExitCode.OK


class NotifyCommand:
    name, help, passthrough = "notify", "send a run's report through notifiers", False
    description = "Send a finished run's report through one or more notifiers."

    def add_arguments(self, parser, app):
        parser.add_argument("run_dir", type=Path, help="the finished run directory")
        add_notify_arguments(parser, app)

    def run(self, args, app) -> int:
        notifiers = notifiers_from(notify_specs(args, app))
        if not notifiers:
            raise UsageError("no notifier: give --notify KIND:TARGET")
        run = RunDir(args.run_dir.resolve())
        outcome = run.outcome()
        if outcome is None:
            raise UsageError(f"{run.path} has no outcome.json")
        deliveries = notify_all(notifiers, RunReport.build(outcome, _comparison(run)))
        return ExitCode.OK if all(d.ok for d in deliveries) else ExitCode.NOT_DELIVERED


def _comparison(run: RunDir) -> Comparison | None:
    body = run.read_json(RunFile.COMPARISON)
    return Comparison.from_dict(body) if body else None


class JobCommand:
    """One unattended run. `<app> run` is a child process so this one outlives it: if the
    run is OOM-killed or overruns, the comparison, publication and notification still happen
    here, and the outcome says `crashed`."""

    name, help, passthrough = "job", "one unattended run: run, compare, publish, notify", True
    description = (
        "One unattended run, as a CronJob or CI runs it: `run` as a child process, then "
        "compare, publish and notify, even if the run crashed. Arguments not listed here "
        "(--profile, -k, --label, --html, ...) are passed through to `run`."
    )
    OWNED = ("--run-dir", "--remote-output", "--work-dir", "--notify", "--compare", "--group")

    def add_arguments(self, parser, app):
        s = app.settings
        parser.add_argument(
            "--work-dir",
            type=Path,
            metavar="DIR",
            default=Path(s.get(Key.WORK_DIR) or "runs"),
            help=f"where the run directory is created (default runs, env {s.name(Key.WORK_DIR)})",
        )
        add_destination_arguments(parser, app)
        parser.add_argument(
            "--fallback-output",
            metavar="URI",
            default=s.get(Key.FALLBACK_URI),
            help="publish here instead when --remote-output cannot be used, e.g. no credentials "
            f"(env {s.name(Key.FALLBACK_URI)})",
        )
        parser.add_argument(
            "--budget",
            type=float,
            metavar="SECONDS",
            default=None,
            help="stop the run after SECONDS; it starts no new case after SECONDS minus "
            f"--grace (default {DEFAULTS.job_budget_s:g}, env {s.name(Key.RUN_BUDGET)}). Keep it "
            "below the Kubernetes Job's activeDeadlineSeconds",
        )
        parser.add_argument(
            "--grace",
            type=float,
            metavar="SECONDS",
            default=DEFAULTS.job_grace_s,
            help=f"time the run has to finish after being stopped (default "
            f"{DEFAULTS.job_grace_s:g})",
        )
        add_baseline_arguments(parser, app)
        add_notify_arguments(parser, app)

    def run(self, args, app) -> int:
        passthrough = list(args.passthrough)
        owned = [a for a in passthrough if a.split("=", 1)[0] in self.OWNED]
        if owned:
            raise UsageError(f"{owned[0]} is set by `job` itself, not passed through to run")
        notifiers = notifiers_from(notify_specs(args, app))
        destination = self._destination(args)
        budget = args.budget or _float_setting(app, Key.RUN_BUDGET, None) or DEFAULTS.job_budget_s

        run_path = (args.work_dir / new_run_id()).resolve()
        # The run stops starting cases `grace` seconds before the job would stop it, so it ends
        # on its own and still writes its outcome; the job only has to step in if it hangs.
        child_budget = max(budget - args.grace, budget / 2)
        child = [
            *app.child_argv("run"),
            "--run-dir",
            str(run_path),
            "--budget",
            f"{child_budget:g}",
            *passthrough,
        ]
        # The job delivers; the child only runs. Its environment loses the delivery settings,
        # so it neither publishes nor notifies a second time.
        delivery = {
            app.settings.name(k)
            for k in (Key.RESULTS_URI, Key.FALLBACK_URI, Key.NOTIFY, Key.RUN_BUDGET)
        }
        env = {k: v for k, v in os.environ.items() if k not in delivery}
        code = self._child(child, env, budget, args.grace)

        run = RunDir.at(run_path)
        outcome = run.outcome()
        if code is None or outcome is None:  # crashed, or died before writing its outcome
            outcome = outcome or RunOutcome(run_id=run.run_id, tool=app.prog)
            outcome.record(run.results())
            outcome.finish(ExitCode.INTERNAL, label="crashed")
            run.save_outcome(outcome)
        verdict = ExitCode.parse(outcome.exit_code)
        if verdict is None:
            verdict = ExitCode.INTERNAL
        group = args.group or outcome.group or "default"

        comparison = None
        baseline = baseline_from(args)
        if baseline and destination:
            try:
                comparison = compare_with_archive(run, destination, group, baseline)
            except Exception as exc:  # noqa: BLE001 -- advisory
                log.warning("comparison failed: %s", exc)
        if destination:
            outcome.publication = deliver(run, destination, group)
            run.save_outcome(outcome)
        delivered = outcome.publication.delivered
        if notifiers:
            report = RunReport.build(outcome, comparison)
            delivered = all(d.ok for d in notify_all(notifiers, report)) and delivered
        final = combine(verdict, delivered)
        emit(f"{app.prog} job: {final.label} (exit {final.value}) -> {run.path}")
        return final

    @staticmethod
    def _destination(args) -> str | None:
        for uri in (args.remote_output, args.fallback_output):
            if not uri:
                continue
            try:
                reason = sink_for(uri).unavailable_reason()
            except UnsupportedDestination as exc:
                raise UsageError(str(exc)) from exc
            if reason is None:
                return uri
            log.warning("cannot publish to %s here: %s", uri, reason)
        log.warning("no usable results destination; results stay local")
        return None

    @staticmethod
    def _child(argv: list[str], env: dict[str, str], budget: float, grace: float) -> int | None:
        """The child's exit status, INTERRUPTED if it was stopped, or None if it crashed (a
        signal, or a status outside the vocabulary)."""
        log.info("job: %s", " ".join(argv))
        process = subprocess.Popen(argv, env=env)  # noqa: S603 -- our own entry point
        try:
            status = process.wait(timeout=budget)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            log.warning("stopping the run (budget %.0fs spent, or interrupted)", budget)
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            return ExitCode.INTERRUPTED
        parsed = ExitCode.parse(status)
        if parsed is None:
            log.error("the run exited with status %s: it crashed", status)
        return parsed


def builtin_commands() -> list[Command]:
    """The commands `App.add_builtins` adds."""
    return [PublishCommand(), CompareCommand(), NotifyCommand(), JobCommand()]
