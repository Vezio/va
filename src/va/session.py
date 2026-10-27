"""Execution of a complete run, with an exit code that identifies the responsible party.

The stages of a run and the exit codes they can produce are:

    preflight              -> 3 PREFLIGHT       No case is run.
    run cases              -> 1 FAILURES        At least one case failed or raised an error.
                           -> 6 INTERRUPTED     SIGINT, SIGTERM or the run budget; finished
                                                cases are kept.
    the tool raised        -> 4 INTERNAL        Not treated as a verdict on the system.
    outcome.json, summary.md, hooks.on_finish
    compare (optional)     -> comparison.json and comparison.md, written before publication
                              so that they are published as well.
    publish (optional)     -> 5 NOT_DELIVERED   Only when the verification exit code was 0.
    notify (optional)      -> 5 NOT_DELIVERED   Only when the verification exit code was 0.

The run directory is written locally in every case and is published regardless of how the run
ended, so that a crashed or interrupted run still leaves evidence. An application's `run`
command builds a `Session` and calls `execute`. All application-specific behaviour is supplied
through the session's fields.
"""

from __future__ import annotations

import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, TypeVar

from . import __version__, preflight
from .compare import Comparison, compare
from .exit_codes import ExitCode, combine
from .hooks import HookChain, ProgressLog, RunHooks
from .layout import RunFile
from .log import emit, get_logger
from .model import RunOutcome, SkipClasses, now
from .notifiers import Notifier, notify_all
from .publish import Archive, deliver
from .report import RunReport
from .rundir import RunDir
from .runner import BudgetExceeded, Case, Runner, Target

log = get_logger("session")
S = TypeVar("S")


@dataclass(frozen=True)
class Baseline:
    """Selection of the earlier published runs that a run is compared with.

    When `run_id` is set, that run is used. Otherwise the latest `window` runs carrying `label`
    are used, so that an ad-hoc run does not become the reference.

    Attributes:
        window: Number of most recent matching runs to compare with.
        label: Label a run must carry to be used as a baseline, or None for any run.
        run_id: Identifier of a specific run to compare with, overriding the other fields.
    """

    window: int = 1
    label: str | None = "trigger=nightly"
    run_id: str | None = None


def compare_with_archive(
    run: RunDir, destination: str, group: str, baseline: Baseline | None = None
) -> Comparison:
    """Compare a local run with baselines from the archive and store the result in the run.

    The comparison is written to comparison.json and comparison.md in the run directory.

    Args:
        run: Run directory, read locally.
        destination: Archive location the baselines are read from.
        group: Archive partition the baselines are read from.
        baseline: Baseline selection. Defaults to the latest run labelled `trigger=nightly`.

    Returns:
        The comparison result.
    """
    baseline = baseline or Baseline()
    baselines = Archive.at(destination).baselines(
        group,
        exclude=run.run_id,
        window=baseline.window,
        label=baseline.label,
        run_id=baseline.run_id,
    )
    result = compare(run.results(), baselines, run.run_id)
    run.write_json(RunFile.COMPARISON, result.to_dict())
    run.write_text(RunFile.COMPARISON_MD, result.markdown())
    return result


@dataclass
class Session(Generic[S]):
    """Configuration and execution of one run, from preflight to notification.

    Attributes:
        tool: Name of the tool, recorded in the outcome and the environment record.
        run: Run directory the run writes to.
        cases: Cases to run.
        targets: Targets the cases run against.
        state: Application state passed to the runner and available to cases.
        subject: Description of the system under test, e.g. `{"name": ..., "digest": ...}`.
        labels: Labels recorded with the run and its results.
        checks: Preflight checks run before any case.
        destination: Publication location (`file://`, `s3://` or a plugin scheme), or None to
            skip publication.
        group: Archive partition the run is published to.
        baseline: Baseline selection for a comparison made before publication. A comparison
            is made only when a destination is also set.
        budget_s: Time in seconds after which no new case is started, or None for no limit.
        workers: Number of cases run concurrently. Exclusive cases still run alone.
        case_timeout_s: Time in seconds after which a case is abandoned, or None for no limit.
        summary_path: File the Markdown report is appended to, e.g. `$GITHUB_STEP_SUMMARY`.
        hooks: Hooks notified of run progress.
        notifiers: Notifiers that receive the final report.
        skip_classes: Skip classes recognised in results.
        links: Links added to the report, e.g. the URL of a CI run.
        env: Additional values recorded in env.json.
    """

    tool: str
    run: RunDir
    cases: Sequence[Case]
    targets: Sequence[Target]
    state: S | None = None
    subject: dict[str, Any] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    checks: Sequence[preflight.Check] = ()
    destination: str | None = None
    group: str = "default"
    baseline: Baseline | None = None
    budget_s: float | None = None
    workers: int = 1
    case_timeout_s: float | None = None
    summary_path: Path | None = None
    hooks: Sequence[RunHooks] = (ProgressLog(),)
    notifiers: Sequence[Notifier] = ()
    skip_classes: SkipClasses = field(default_factory=SkipClasses.default)
    links: dict[str, str] = field(default_factory=dict)
    env: dict[str, Any] = field(default_factory=dict)

    def execute(self) -> ExitCode:
        """Run the session and return its final exit code.

        The outcome, summary and optional comparison are written to the run directory, the run
        is published when a destination is set, and notifiers are called. A delivery failure
        changes the exit code to NOT_DELIVERED only when verification itself succeeded.

        Returns:
            The verification exit code combined with the delivery status.
        """
        hooks = HookChain(self.hooks)
        outcome = RunOutcome(
            run_id=self.run.run_id,
            tool=self.tool,
            labels=dict(self.labels),
            subject=dict(self.subject),
            group=self.group,
            links=dict(self.links),
        )
        self.run.write_json(
            RunFile.ENV,
            {"tool": self.tool, "tool_version": __version__, "started": now(), **self.env},
        )
        code = ExitCode.INTERNAL
        try:
            code = self._verify(outcome, hooks)
        except (KeyboardInterrupt, BudgetExceeded) as exc:
            log.warning("%s; keeping what finished", exc or "interrupted")
            code = ExitCode.INTERRUPTED
        except Exception:  # noqa: BLE001
            # An unexpected exception is a defect in the tool.
            log.error("internal error:\n%s", traceback.format_exc())
            code = ExitCode.INTERNAL
        finally:
            outcome.record(self.run.results())
            outcome.finish(code)
            self.run.save_outcome(outcome)

        comparison = self._compare()
        self.run.write_text(RunFile.SUMMARY, RunReport.build(outcome, comparison).markdown())
        hooks.on_finish(self.run, outcome)

        if self.destination:
            outcome.publication = deliver(self.run, self.destination, self.group)
            # Save again so that the local copy records the publication status.
            self.run.save_outcome(outcome)
            hooks.on_published(self.run, outcome.publication)
        report = RunReport.build(outcome, comparison)
        if self.summary_path:
            with self.summary_path.open("a", encoding="utf-8") as handle:
                handle.write(report.markdown())

        delivered = outcome.publication.delivered
        if self.notifiers:
            delivered = all(d.ok for d in notify_all(self.notifiers, report)) and delivered
        final = combine(code, delivered)
        emit(f"{self.tool}: {final.label} (exit {final.value}) -> {self.run.path}")
        return final

    def _verify(self, outcome: RunOutcome, hooks: HookChain) -> ExitCode:
        """Run the preflight checks and then the cases.

        Args:
            outcome: Outcome that receives the preflight problems.
            hooks: Hooks notified when the cases start.

        Returns:
            PREFLIGHT if a blocking preflight problem was found, USAGE if no case or target was
            selected, FAILURES if any case failed, and OK otherwise.
        """
        problems = preflight.run(self.checks)
        outcome.preflight = problems
        if preflight.blocking(problems):
            log.error("preflight failed: nothing was tested")
            return ExitCode.PREFLIGHT
        if not self.cases or not self.targets:
            log.error("nothing selected: an empty run is never a pass")
            return ExitCode.USAGE
        hooks.on_start(self.run, self.cases, self.targets)
        runner: Runner[S] = Runner(
            self.run,
            state=self.state,
            labels=self.labels,
            hooks=hooks,
            skip_classes=self.skip_classes,
            workers=self.workers,
            case_timeout_s=self.case_timeout_s,
        )
        if self.budget_s:
            runner.deadline = runner.clock() + self.budget_s
        records = runner.run_all(self.cases, self.targets)
        return ExitCode.FAILURES if any(r.verdict.is_failure for r in records) else ExitCode.OK

    def _compare(self) -> Comparison | None:
        """Compare the run with its baselines when a baseline and a destination are set.

        A failed comparison is logged and ignored, because the comparison is advisory.

        Returns:
            The comparison, or None if none was configured or it failed.
        """
        if not (self.baseline and self.destination):
            return None
        try:
            return compare_with_archive(self.run, self.destination, self.group, self.baseline)
        except Exception as exc:  # noqa: BLE001
            # The comparison is advisory.
            log.warning("comparison failed: %s", exc)
            return None
