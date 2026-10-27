"""One run, end to end, with the exit code saying who is to blame.

    preflight              -> 3 PREFLIGHT       nothing is tested
    run cases              -> 1 FAILURES        any fail or error
                           -> 6 INTERRUPTED     SIGINT/SIGTERM or the budget; finished cases kept
    the tool raised        -> 4 INTERNAL        never a verdict
    outcome.json, summary.md, hooks.on_finish
    compare (optional)     -> comparison.json/md, before publication so it is published too
    publish (optional)     -> 5 NOT_DELIVERED   only when the verdict was 0
    notify (optional)      -> 5 NOT_DELIVERED   only when the verdict was 0

The run directory is written locally whatever happens and published however the run ended, so
a crashed or interrupted run still leaves evidence. An app's `run` command builds a `Session`
and calls `execute`; everything app-specific comes in through its fields.
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
from .log import emit, get_logger
from .model import DEFAULT_SKIP_CLASSES, RunOutcome, SkipClasses, now
from .notifiers import Notifier, notify_all
from .publish import Archive, deliver
from .report import RunReport
from .rundir import COMPARISON, COMPARISON_MD, ENV, SUMMARY, RunDir
from .runner import BudgetExceeded, Case, Runner, Target

log = get_logger("session")
S = TypeVar("S")


@dataclass(frozen=True)
class Baseline:
    """Which earlier published runs to compare with: the one named `run_id`, or the latest
    `window` runs carrying `label` (so an ad-hoc run never becomes the reference)."""

    window: int = 1
    label: str | None = "trigger=nightly"
    run_id: str | None = None


NIGHTLY = Baseline()  # the latest run labelled trigger=nightly


def compare_with_archive(
    run: RunDir, destination: str, group: str, baseline: Baseline = NIGHTLY
) -> Comparison:
    """Compare `run` (read locally) with the archive; write comparison.json and .md into it."""
    baselines = Archive.at(destination).baselines(
        group,
        exclude=run.run_id,
        window=baseline.window,
        label=baseline.label,
        run_id=baseline.run_id,
    )
    result = compare(run.results(), baselines, run.run_id)
    run.write_json(COMPARISON, result.to_dict())
    run.write_text(COMPARISON_MD, result.markdown())
    return result


@dataclass
class Session(Generic[S]):
    tool: str
    run: RunDir
    cases: Sequence[Case]
    targets: Sequence[Target]
    state: S | None = None
    subject: dict[str, Any] = field(default_factory=dict)  # {"name": ..., "digest": ...}
    labels: dict[str, str] = field(default_factory=dict)
    checks: Sequence[preflight.Check] = ()
    destination: str | None = None  # where to publish: file://, s3://, a plugin scheme
    group: str = "default"  # archive partition
    baseline: Baseline | None = None  # compare before publishing, when a destination is set
    budget_s: float | None = None
    workers: int = 1  # opt-in concurrency; exclusive cases still run alone
    case_timeout_s: float | None = None  # opt-in: abandon a case after this long
    summary_path: Path | None = None  # e.g. $GITHUB_STEP_SUMMARY
    hooks: Sequence[RunHooks] = (ProgressLog(),)
    notifiers: Sequence[Notifier] = ()
    skip_classes: SkipClasses = DEFAULT_SKIP_CLASSES
    links: dict[str, str] = field(default_factory=dict)  # added to the report, e.g. a CI run
    env: dict[str, Any] = field(default_factory=dict)  # recorded in env.json

    def execute(self) -> ExitCode:
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
            ENV, {"tool": self.tool, "tool_version": __version__, "started": now(), **self.env}
        )
        code = ExitCode.INTERNAL
        try:
            code = self._verify(outcome, hooks)
        except (KeyboardInterrupt, BudgetExceeded) as exc:
            log.warning("%s; keeping what finished", exc or "interrupted")
            code = ExitCode.INTERRUPTED
        except Exception:  # noqa: BLE001 -- a defect in the tool, said as such
            log.error("internal error:\n%s", traceback.format_exc())
            code = ExitCode.INTERNAL
        finally:
            outcome.record(self.run.results())
            outcome.finish(code)
            self.run.save_outcome(outcome)

        comparison = self._compare()
        self.run.write_text(SUMMARY, RunReport.build(outcome, comparison).markdown())
        hooks.on_finish(self.run, outcome)

        if self.destination:
            outcome.publication = deliver(self.run, self.destination, self.group)
            self.run.save_outcome(outcome)  # the local copy learns how the upload went
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
        if not (self.baseline and self.destination):
            return None
        try:
            return compare_with_archive(self.run, self.destination, self.group, self.baseline)
        except Exception as exc:  # noqa: BLE001 -- a comparison is advisory
            log.warning("comparison failed: %s", exc)
            return None
