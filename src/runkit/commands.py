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

from .cli import App, UsageError
from .compare import Comparison
from .exit_codes import ExitCode, combine
from .hooks import ProgressLog, RunHooks
from .log import emit, get_logger
from .model import RunOutcome
from .notifiers import Notifier, NotifierError, notify_all, parse_specs
from .outputs import HtmlReport, JUnitReport, MetricsFile
from .publish import deliver
from .report import RunReport
from .rundir import COMPARISON, RunDir, new_run_id
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


def add_destination_arguments(parser: Arguments, app: App) -> None:
    s = app.settings
    parser.add_argument(
        "--remote-output",
        default=s.get(Key.RESULTS_URI),
        help=f"publish to this URI: file:///path, s3://bucket/prefix or a plugin "
        f"scheme (env {s.name(Key.RESULTS_URI)}); unset keeps results local",
    )
    parser.add_argument(
        "--group",
        default=s.get(Key.GROUP),
        help=f"archive partition, e.g. the profile name (env {s.name(Key.GROUP)})",
    )


def add_notify_arguments(parser: Arguments, app: App) -> None:
    s = app.settings
    parser.add_argument(
        "--notify",
        action="append",
        default=s.get_list(Key.NOTIFY),
        metavar="KIND:TARGET",
        help="notifier spec, repeatable: slack:env:VAR, webhook:URL, exec:CMD, "
        f"file:PATH, log: (env {s.name(Key.NOTIFY)}, comma-separated)",
    )


def add_baseline_arguments(parser: Arguments, app: App) -> None:
    parser.add_argument(
        "--compare",
        action="store_true",
        help="compare with earlier published runs before publishing",
    )
    parser.add_argument(
        "--compare-window",
        type=int,
        default=1,
        help="earlier runs that must agree before a change is a regression",
    )
    parser.add_argument(
        "--baseline-label",
        default=app.settings.get(Key.BASELINE_LABEL, "trigger=nightly"),
        help="only runs with this KEY=VALUE label are baselines",
    )
    parser.add_argument("--baseline", default=None, help="one run id to compare with instead")


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
        default=Path(s.get(Key.WORK_DIR) or "runs"),
        help=f"where run directories are created (env {s.name(Key.WORK_DIR)})",
    )
    out.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="the exact run directory to create (set by `job`)",
    )
    add_destination_arguments(out, app)
    out.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="append the Markdown summary here (e.g. $GITHUB_STEP_SUMMARY)",
    )
    out.add_argument(
        "--label",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="label the run, e.g. trigger=nightly (repeatable)",
    )
    out.add_argument(
        "--link",
        action="append",
        default=[],
        metavar="NAME=URL",
        help="a link for the report, e.g. ci=https://... (repeatable)",
    )
    add_notify_arguments(out, app)
    add_baseline_arguments(parser.add_argument_group("comparison"), app)
    add_execution_arguments(parser.add_argument_group("execution (opt-in)"), app)
    add_output_arguments(parser.add_argument_group("reports (opt-in)"), app)
    sel = parser.add_argument_group("selection")
    sel.add_argument(
        "-k",
        dest="patterns",
        action="append",
        default=[],
        help="run cases whose id contains this (repeatable)",
    )
    sel.add_argument(
        "--tag",
        dest="tags",
        action="append",
        default=[],
        help="run only cases with this tag (repeatable: all must match)",
    )
    sel.add_argument("--exclude-tag", dest="exclude_tags", action="append", default=[])
    sel.add_argument(
        "--budget",
        type=float,
        default=None,
        help=f"seconds after which no new case starts (env {s.name(Key.RUN_BUDGET)})",
    )


def _int_setting(app: App, key: Key, default: int | None) -> int | None:
    try:
        return app.settings.get_int(key, default)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc


def add_execution_arguments(parser: Arguments, app: App) -> None:
    """Opt-in execution controls; the defaults run one case at a time with no timeout."""
    s = app.settings
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"run cases concurrently on this many threads (env "
        f"{s.name(Key.WORKERS)}; default 1). Cases marked exclusive run alone",
    )
    parser.add_argument(
        "--case-timeout",
        type=float,
        default=None,
        help=f"seconds before a case is abandoned and recorded as an error (env "
        f"{s.name(Key.CASE_TIMEOUT)}; default none)",
    )


def add_output_arguments(parser: Arguments, app: App) -> None:
    """Opt-in run outputs, written into the run directory and published with it."""
    parser.add_argument(
        "--html",
        nargs="?",
        const=True,
        default=None,
        metavar="PATH",
        help="write report.html (and a copy to PATH if given)",
    )
    parser.add_argument(
        "--junit",
        nargs="?",
        const=True,
        default=None,
        metavar="PATH",
        help="write junit.xml for CI (and a copy to PATH if given)",
    )
    parser.add_argument(
        "--metrics-file",
        type=Path,
        default=app.settings.get(Key.METRICS_FILE),
        help="write Prometheus metrics here, atomically (env "
        f"{app.settings.name(Key.METRICS_FILE)})",
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
        notifiers = notifiers_from(args.notify)
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

    def add_arguments(self, parser, app):
        parser.add_argument("run_dir", type=Path)
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

    def add_arguments(self, parser, app):
        parser.add_argument("run_dir", type=Path)
        add_destination_arguments(parser, app)
        add_baseline_arguments(parser, app)
        parser.add_argument("--fail-on-regression", action="store_true")

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

    def add_arguments(self, parser, app):
        parser.add_argument("run_dir", type=Path)
        add_notify_arguments(parser, app)

    def run(self, args, app) -> int:
        notifiers = notifiers_from(args.notify)
        if not notifiers:
            raise UsageError("no notifier: give --notify KIND:TARGET")
        run = RunDir(args.run_dir.resolve())
        outcome = run.outcome()
        if outcome is None:
            raise UsageError(f"{run.path} has no outcome.json")
        deliveries = notify_all(notifiers, RunReport.build(outcome, _comparison(run)))
        return ExitCode.OK if all(d.ok for d in deliveries) else ExitCode.NOT_DELIVERED


def _comparison(run: RunDir) -> Comparison | None:
    body = run.read_json(COMPARISON)
    return Comparison.from_dict(body) if body else None


class JobCommand:
    """One unattended run. `<app> run` is a child process so this one outlives it: if the
    run is OOM-killed or overruns, the comparison, publication and notification still happen
    here, and the outcome says `crashed`."""

    name, help, passthrough = "job", "one unattended run: run, compare, publish, notify", True
    OWNED = ("--run-dir", "--remote-output", "--work-dir", "--notify", "--compare", "--group")

    def add_arguments(self, parser, app):
        s = app.settings
        parser.add_argument("--work-dir", type=Path, default=Path(s.get(Key.WORK_DIR) or "runs"))
        add_destination_arguments(parser, app)
        parser.add_argument(
            "--fallback-output",
            default=s.get(Key.FALLBACK_URI),
            help="file:///path used only when --remote-output is unusable here",
        )
        parser.add_argument(
            "--budget",
            type=float,
            default=None,
            help=f"seconds before the child is stopped (env "
            f"{s.name(Key.RUN_BUDGET)}, default 3000); keep it below the "
            "Job's activeDeadlineSeconds",
        )
        parser.add_argument(
            "--grace",
            type=float,
            default=60,
            help="seconds between SIGTERM and SIGKILL for the child",
        )
        add_baseline_arguments(parser, app)
        add_notify_arguments(parser, app)

    def run(self, args, app) -> int:
        passthrough = list(args.passthrough)
        owned = [a for a in passthrough if a.split("=", 1)[0] in self.OWNED]
        if owned:
            raise UsageError(f"{owned[0]} is set by `job` itself, not passed through to run")
        notifiers = notifiers_from(args.notify)
        destination = self._destination(args)
        budget = args.budget or _float_setting(app, Key.RUN_BUDGET, None) or 3000.0

        run_path = (args.work_dir / new_run_id()).resolve()
        child = [*app.child_argv("run"), "--run-dir", str(run_path), *passthrough]
        # The job delivers; the child only runs. Its environment loses the delivery settings,
        # so it neither publishes nor notifies a second time.
        delivery = {app.settings.name(k) for k in (Key.RESULTS_URI, Key.FALLBACK_URI, Key.NOTIFY)}
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


BUILTINS = (PublishCommand, CompareCommand, NotifyCommand, JobCommand)
