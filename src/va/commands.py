"""Built-in commands and the standard flags of an application's `run` command.

The built-in commands are:

    publish  RUN_DIR    Publish a finished run directory, uploading the manifest last.
    compare  RUN_DIR    Compare a run with earlier published runs.
    notify   RUN_DIR    Send a run's report through the configured notifiers.
    job                 Perform one unattended run: start `<app> run` as a child process, then
                        compare, publish and notify, also after a crash. This is the command
                        a CronJob or CI workflow runs.

Each flag takes its default from `App.settings` (`<PREFIX>_<KEY>`), so a flag and its
environment variable configure the same option. An application's `run` command calls
`add_run_arguments` and `RunOptions.from_args`.
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
    """Object that flags can be added to, such as an argparse parser or argument group."""

    def add_argument(self, *args: Any, **kwargs: Any) -> Any:
        """Add an argument, with the same signature as `argparse.ArgumentParser.add_argument`.

        Args:
            *args: Positional arguments for `add_argument`.
            **kwargs: Keyword arguments for `add_argument`.

        Returns:
            The created action.
        """
        ...


def parse_labels(values: list[str]) -> dict[str, str]:
    """Parse `KEY=VALUE` items into a dictionary.

    Args:
        values: Items in the form `KEY=VALUE`. The value may be empty.

    Returns:
        The parsed pairs. A later item overrides an earlier one with the same key.

    Raises:
        UsageError: If an item has no `=` or an empty key.
    """
    labels = {}
    for item in values:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise UsageError(f"--label takes KEY=VALUE, not {item!r}")
        labels[key] = value
    return labels


def notifiers_from(specs: list[str]) -> list[Notifier]:
    """Build notifiers from their specifications.

    Args:
        specs: Notifier specifications in the form `KIND:TARGET`.

    Returns:
        The notifiers, in the order given.

    Raises:
        UsageError: If a specification is invalid.
    """
    try:
        return parse_specs(specs)
    except NotifierError as exc:
        raise UsageError(f"--notify: {exc}") from exc


def _float_setting(app: App, key: Key, default: float | None) -> float | None:
    """Read a numeric setting and report an invalid value as a usage error.

    Args:
        app: Application whose settings are read.
        key: Setting key.
        default: Value returned when the setting is unset.

    Returns:
        The parsed value, or `default`.

    Raises:
        UsageError: If the value is not a number.
    """
    try:
        return app.settings.get_float(key, default)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc


def add_destination_arguments(parser: Arguments, app: App, *, reading: bool = False) -> None:
    """Add the `--remote-output` and `--group` flags.

    Args:
        parser: Parser or argument group to add the flags to.
        app: Application whose settings provide the defaults.
        reading: True to word the help for commands that read the archive rather than
            publish to it.
    """
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
    """Add the repeatable `--notify` flag.

    Args:
        parser: Parser or argument group to add the flag to.
        app: Application whose settings name the corresponding environment variable.
    """
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
    """Return the notifier specifications for a command.

    Args:
        args: Parsed arguments.
        app: Application whose settings are read.

    Returns:
        The `--notify` values if any were given, otherwise the specifications from the
        environment.
    """
    return list(args.notify) if args.notify else app.settings.get_list(Key.NOTIFY)


def add_baseline_arguments(parser: Arguments, app: App, *, switch: bool = True) -> None:
    """Add the flags that select baselines for a comparison.

    Args:
        parser: Parser or argument group to add the flags to.
        app: Application whose settings provide the defaults.
        switch: True to also add `--compare`, for commands where the comparison is optional.
    """
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
    """Build the baseline selection from parsed comparison flags.

    Args:
        args: Parsed arguments that include `--compare` and the baseline flags.

    Returns:
        The baseline selection, or None if `--compare` was not given.
    """
    if not args.compare:
        return None
    return Baseline(args.compare_window, args.baseline_label or None, args.baseline)


def add_run_arguments(parser: argparse.ArgumentParser, app: App) -> None:
    """Add the standard flags of an application's `run` command.

    All applications share these flags, so that `job`, CI workflows and interactive users
    drive every application in the same way.

    Args:
        parser: Parser of the `run` command.
        app: Application whose settings provide the defaults.
    """
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
    """Read an integer setting and report an invalid value as a usage error.

    Args:
        app: Application whose settings are read.
        key: Setting key.
        default: Value returned when the setting is unset.

    Returns:
        The parsed value, or `default`.

    Raises:
        UsageError: If the value is not a whole number.
    """
    try:
        return app.settings.get_int(key, default)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc


def add_execution_arguments(parser: Arguments, app: App) -> None:
    """Add the flags that control how cases are executed.

    By default cases run one at a time, without a per-case timeout and without a run budget.

    Args:
        parser: Parser or argument group to add the flags to.
        app: Application whose settings name the corresponding environment variables.
    """
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
    """Add the flags for optional run outputs.

    The outputs are written into the run directory and published with it.

    Args:
        parser: Parser or argument group to add the flags to.
        app: Application whose settings provide the defaults.
    """
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
    """Typed values of the standard run flags.

    Attributes:
        run: Run directory created for the run.
        destination: Publication location, or None to keep results local.
        group: Archive partition, or None if not set.
        summary: File the Markdown summary is appended to, or None.
        labels: Labels attached to the run.
        links: Named links shown in the report and notifications.
        selector: Case selection from `-k`, `--tag` and `--exclude-tag`.
        budget_s: Time in seconds after which no new case is started, or None.
        notifiers: Notifiers that receive the report.
        baseline: Baseline selection for a comparison, or None if no comparison was requested.
        workers: Number of cases run concurrently.
        case_timeout_s: Time in seconds after which a case is abandoned, or None.
        outputs: Hooks that write the optional outputs requested by flags.
    """

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
        """Return the run hooks: the default progress log followed by the requested outputs."""
        return [ProgressLog(), *self.outputs]

    @classmethod
    def from_args(cls, args: argparse.Namespace, app: App) -> RunOptions:
        """Validate the parsed flags and create the run directory.

        All validation happens before the run directory is created, so that a usage error
        does not leave a directory behind. When logging is configured, a redacted run.log is
        added to the run directory and published with it.

        Args:
            args: Arguments parsed by a parser prepared with `add_run_arguments`.
            app: Application whose settings supply values for unset flags.

        Returns:
            The run options.

        Raises:
            UsageError: If a label, link, notifier, worker count, timeout or destination is
                invalid.
        """
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
            app.logging.add_file(run.file("run.log"))
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


class PublishCommand:
    """Built-in `publish` command, which publishes a finished run directory.

    Attributes:
        name: Subcommand name.
        help: One-line help text.
        passthrough: False, because the command accepts no unknown arguments.
        description: Full description shown in the command's help.
    """

    name, help, passthrough = "publish", "publish a finished run directory (manifest last)", False
    description = (
        "Publish a finished run directory to --remote-output, under <group>/<run id>/, with "
        "MANIFEST.json uploaded last. Records the result in the run's outcome.json."
    )

    def add_arguments(self, parser, app):
        """Add the run directory argument and the destination flags.

        Args:
            parser: Subparser for this command.
            app: Application the command belongs to.
        """
        parser.add_argument("run_dir", type=Path, help="the run directory to publish")
        add_destination_arguments(parser, app)

    def run(self, args, app) -> int:
        """Publish the run directory and record the publication in its outcome.

        Args:
            args: Parsed arguments.
            app: Application the command belongs to.

        Returns:
            OK if the run was delivered, otherwise NOT_DELIVERED.

        Raises:
            UsageError: If the destination or the group is not set.
        """
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
    """Built-in `compare` command, which compares a run with earlier published runs.

    Attributes:
        name: Subcommand name.
        help: One-line help text.
        passthrough: False, because the command accepts no unknown arguments.
        description: Full description shown in the command's help.
    """

    name, help, passthrough = "compare", "compare a run with earlier published runs", False
    description = (
        "Compare a run directory with earlier runs published in the same group, and write "
        "comparison.json and comparison.md into it."
    )

    def add_arguments(self, parser, app):
        """Add the run directory argument and the archive and baseline flags.

        Args:
            parser: Subparser for this command.
            app: Application the command belongs to.
        """
        parser.add_argument("run_dir", type=Path, help="the run directory to compare")
        add_destination_arguments(parser, app, reading=True)
        add_baseline_arguments(parser, app, switch=False)
        parser.add_argument(
            "--fail-on-regression",
            action="store_true",
            help="exit 1 when there is a regression (default: report it and exit 0)",
        )

    def run(self, args, app) -> int:
        """Compare the run with its baselines and print the comparison.

        Args:
            args: Parsed arguments.
            app: Application the command belongs to.

        Returns:
            FAILURES if `--fail-on-regression` was given and a regression was found,
            otherwise OK.

        Raises:
            UsageError: If the archive location or the group is not set.
        """
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
    """Built-in `notify` command, which sends a finished run's report through notifiers.

    Attributes:
        name: Subcommand name.
        help: One-line help text.
        passthrough: False, because the command accepts no unknown arguments.
        description: Full description shown in the command's help.
    """

    name, help, passthrough = "notify", "send a run's report through notifiers", False
    description = "Send a finished run's report through one or more notifiers."

    def add_arguments(self, parser, app):
        """Add the run directory argument and the `--notify` flag.

        Args:
            parser: Subparser for this command.
            app: Application the command belongs to.
        """
        parser.add_argument("run_dir", type=Path, help="the finished run directory")
        add_notify_arguments(parser, app)

    def run(self, args, app) -> int:
        """Send the run's report, including a stored comparison if there is one.

        Args:
            args: Parsed arguments.
            app: Application the command belongs to.

        Returns:
            OK if every notifier delivered the report, otherwise NOT_DELIVERED.

        Raises:
            UsageError: If no notifier is configured or the run has no outcome.json.
        """
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
    """Load the comparison stored in a run directory.

    Args:
        run: Run directory to read.

    Returns:
        The comparison, or None if the run has no comparison.json.
    """
    body = run.read_json(RunFile.COMPARISON)
    return Comparison.from_dict(body) if body else None


class JobCommand:
    """Built-in `job` command, which performs one unattended run.

    `<app> run` is started as a child process so that this process outlives it. If the run is
    killed, for example for running out of memory, or exceeds its budget, the comparison,
    publication and notification are still performed here, and the outcome is labelled
    `crashed`.

    Attributes:
        name: Subcommand name.
        help: One-line help text.
        passthrough: True, because unknown arguments are forwarded to `run`.
        description: Full description shown in the command's help.
        OWNED: Flags that `job` sets on the child itself and that therefore cannot be passed
            through.
    """

    name, help, passthrough = "job", "one unattended run: run, compare, publish, notify", True
    description = (
        "One unattended run, as a CronJob or CI runs it: `run` as a child process, then "
        "compare, publish and notify, even if the run crashed. Arguments not listed here "
        "(--profile, -k, --label, --html, ...) are passed through to `run`."
    )
    OWNED = ("--run-dir", "--remote-output", "--work-dir", "--notify", "--compare", "--group")

    def add_arguments(self, parser, app):
        """Add the flags for the work directory, destinations, budget, comparison and notifiers.

        Args:
            parser: Subparser for this command.
            app: Application the command belongs to.
        """
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
        """Run the child process, then compare, publish and notify.

        The delivery settings are removed from the child's environment, so that only this
        process publishes and notifies. If the child crashes or exits without writing an
        outcome, an outcome labelled `crashed` is written from the results it recorded.

        Args:
            args: Parsed arguments, including the arguments passed through to `run`.
            app: Application the command belongs to.

        Returns:
            The run's exit code combined with the delivery status.

        Raises:
            UsageError: If a passed-through argument is one that `job` sets itself, or a
                destination is not supported.
        """
        passthrough = list(args.passthrough)
        owned = [a for a in passthrough if a.split("=", 1)[0] in self.OWNED]
        if owned:
            raise UsageError(f"{owned[0]} is set by `job` itself, not passed through to run")
        notifiers = notifiers_from(notify_specs(args, app))
        destination = self._destination(args)
        budget = args.budget or _float_setting(app, Key.RUN_BUDGET, None) or DEFAULTS.job_budget_s

        run_path = (args.work_dir / new_run_id()).resolve()
        # The child stops starting cases `grace` seconds before the job would stop it. The child
        # therefore normally ends by itself and writes its outcome, and the job only needs to
        # intervene if the child hangs.
        child_budget = max(budget - args.grace, budget / 2)
        child = [
            *app.child_argv("run"),
            "--run-dir",
            str(run_path),
            "--budget",
            f"{child_budget:g}",
            *passthrough,
        ]
        # Only the job publishes and notifies. The delivery settings are removed from the
        # child's environment so that the child does not deliver the results a second time.
        delivery = {
            app.settings.name(k)
            for k in (Key.RESULTS_URI, Key.FALLBACK_URI, Key.NOTIFY, Key.RUN_BUDGET)
        }
        env = {k: v for k, v in os.environ.items() if k not in delivery}
        code = self._child(child, env, budget, args.grace)

        run = RunDir.at(run_path)
        outcome = run.outcome()
        # The child crashed, or exited before writing its outcome.
        if code is None or outcome is None:
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
            except Exception as exc:  # noqa: BLE001
                # The comparison is advisory.
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
        """Choose the first usable destination from `--remote-output` and `--fallback-output`.

        Args:
            args: Parsed arguments.

        Returns:
            The first destination that is usable in this environment, or None if neither is,
            in which case results stay local.

        Raises:
            UsageError: If a destination uses an unsupported scheme.
        """
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
        """Run the child process within the budget and return its exit code.

        When the budget is spent or the job is interrupted, the child receives SIGTERM and, if
        it has not exited after `grace` seconds, SIGKILL.

        Args:
            argv: Command line of the child process.
            env: Environment of the child process.
            budget: Time in seconds the child may run before it is stopped.
            grace: Time in seconds between SIGTERM and SIGKILL.

        Returns:
            The child's exit code, INTERRUPTED if it was stopped, or None if it crashed, that
            is, it was killed by a signal or exited with a status that is not an `ExitCode`.
        """
        log.info("job: %s", " ".join(argv))
        # The command is this tool's own entry point.
        process = subprocess.Popen(argv, env=env)  # noqa: S603
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
    """Return new instances of the built-in commands registered by `App.add_builtins`."""
    return [PublishCommand(), CompareCommand(), NotifyCommand(), JobCommand()]
