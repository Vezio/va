"""Lifecycle hooks that let an application or plugin react to a run.

Subclass `RunHooks` and override the methods required, for example to stream results to a
database, render a live table, emit metrics, or write an additional report into the run directory
before it is published.

Example:
    class Metrics(RunHooks):
        def on_result(self, run, record): counter.labels(record.verdict).inc()

Hooks are observers. An exception raised by a hook is logged and ignored, so that a report or a
metric cannot change a verdict.
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
    """Base class for run hooks.

    Every method is a no-op. Subclasses override the methods they need.
    """

    def on_start(self, run: RunDir, cases: Sequence[Case], targets: Sequence[Target]) -> None:
        """Handle the start of a run, called after preflight passes and before the first case.

        Args:
            run: The run directory.
            cases: The cases selected for the run.
            targets: The targets the cases run against.
        """

    def on_result(self, run: RunDir, record: ResultRecord) -> None:
        """Handle the result of one case, called after its record is written.

        Args:
            run: The run directory.
            record: The result record.
        """

    def on_finish(self, run: RunDir, outcome: RunOutcome) -> None:
        """Handle the end of a run, called after outcome.json is written and before publication.

        Files written to the run directory here are published with the run.

        Args:
            run: The run directory.
            outcome: The outcome of the run.
        """

    def on_published(self, run: RunDir, publication: Publication) -> None:
        """Handle the result of publication, called after it is attempted, whatever its status.

        Args:
            run: The run directory.
            publication: The publication result.
        """


class HookChain(RunHooks):
    """Composite hook that forwards each call to several hooks.

    Each hook is called independently, so an exception in one does not prevent the others from
    being called.

    Args:
        hooks: The hooks to call, in order.

    Attributes:
        hooks: The hooks to call, in order.
    """

    def __init__(self, hooks: Sequence[RunHooks] = ()):
        self.hooks = list(hooks)

    def _each(self, method: str, *args) -> None:
        """Call a method on every hook, logging and ignoring any exception it raises.

        Args:
            method: Name of the hook method to call.
            *args: Arguments passed to the method.
        """
        for hook in self.hooks:
            try:
                getattr(hook, method)(*args)
            # A hook must not change the verdict, so its errors are only logged.
            except Exception as exc:  # noqa: BLE001
                log.warning("%s.%s failed: %s", type(hook).__name__, method, exc)

    def on_start(self, run, cases, targets) -> None:
        """Forward `on_start` to every hook."""
        self._each("on_start", run, cases, targets)

    def on_result(self, run, record) -> None:
        """Forward `on_result` to every hook."""
        self._each("on_result", run, record)

    def on_finish(self, run, outcome) -> None:
        """Forward `on_finish` to every hook."""
        self._each("on_finish", run, outcome)

    def on_published(self, run, publication) -> None:
        """Forward `on_published` to every hook."""
        self._each("on_published", run, publication)


class ProgressLog(RunHooks):
    """Default progress display that logs one diagnostic line per case."""

    def on_start(self, run, cases, targets) -> None:
        """Log the run identifier and the number of planned checks."""
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
        """Log the verdict, target and case of one result."""
        variant = f"[{record.variant}]" if record.variant else ""
        log.info(
            "  %-5s %-12s %s%s",
            record.verdict.value.upper(),
            record.target,
            record.case_id,
            variant,
        )
