"""Lifecycle hooks: how an app (or a plugin) reacts to a run without forking the framework.

Subclass `RunHooks` and override what you need: stream results to a database, render a live
table, emit metrics, write an extra report into the run directory before it is published.

    class Metrics(RunHooks):
        def on_result(self, record): counter.labels(record.verdict).inc()

Hooks are observers: a hook that raises is logged and ignored, because a report or a metric
must never change a verdict.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from .log import get_logger
from .model import Publication, ResultRecord, RunOutcome

if TYPE_CHECKING:
    from .rundir import RunDir
    from .runner import Case, Target

log = get_logger("hooks")


class RunHooks:
    """Every method is a no-op; override the ones you need."""

    def on_start(self, run: RunDir, cases: Sequence[Case], targets: Sequence[Target]) -> None:
        """Before the first case, after preflight passed."""

    def on_result(self, run: RunDir, record: ResultRecord) -> None:
        """After each case, once its record is written."""

    def on_finish(self, run: RunDir, outcome: RunOutcome) -> None:
        """After outcome.json is written and before publication: files written here are
        published with the run."""

    def on_published(self, run: RunDir, publication: Publication) -> None:
        """After publication was attempted, whatever its status."""


class HookChain(RunHooks):
    """Several hooks as one; each call is isolated from the others' failures."""

    def __init__(self, hooks: Sequence[RunHooks] = ()):
        self.hooks = list(hooks)

    def _each(self, method: str, *args) -> None:
        for hook in self.hooks:
            try:
                getattr(hook, method)(*args)
            except Exception as exc:  # noqa: BLE001 -- a hook never changes the verdict
                log.warning("%s.%s failed: %s", type(hook).__name__, method, exc)

    def on_start(self, run, cases, targets) -> None:
        self._each("on_start", run, cases, targets)

    def on_result(self, run, record) -> None:
        self._each("on_result", run, record)

    def on_finish(self, run, outcome) -> None:
        self._each("on_finish", run, outcome)

    def on_published(self, run, publication) -> None:
        self._each("on_published", run, publication)


class ProgressLog(RunHooks):
    """One diagnostic line per case: the default progress display."""

    def on_start(self, run, cases, targets) -> None:
        planned = sum(len(c.variants) for c in cases) * len(targets)
        log.info(
            "run %s: %d %s (%d %s x %d %s)",
            run.run_id,
            planned,
            "check" if planned == 1 else "checks",
            len(cases),
            "case" if len(cases) == 1 else "cases",
            len(targets),
            "target" if len(targets) == 1 else "targets",
        )

    def on_result(self, run, record) -> None:
        variant = f"[{record.variant}]" if record.variant else ""
        log.info(
            "  %-5s %-12s %s%s",
            record.verdict.value.upper(),
            record.target,
            record.case_id,
            variant,
        )
