"""A Prometheus text-format metrics file for each run.

Point it at node_exporter's textfile collector directory (or any scraper that reads the
format) and run health lands on the same dashboards and alerts as the infrastructure:

    va_run_exit_code{tool,group}             the last run's exit code
    va_run_timestamp_seconds{tool,group}     when it finished
    va_run_duration_seconds{tool,group}
    va_cases{tool,group,target,verdict}      cases by verdict
    va_skips{tool,group,skip_class}          skips by class
    va_run_published{tool,group}             1 if published (or not asked)

The prefix is configurable: an app usually passes its own name.

The file is written atomically (a temporary file renamed into place), so a scraper never reads
half of it. Written after publication when there is one, so `published` is known.
"""

from __future__ import annotations

import os
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from ..hooks import RunHooks
from ..layout import RunFile
from ..model import Publication, ResultRecord, RunOutcome
from ..rundir import RunDir


def _labels(**labels: str) -> str:
    def esc(value: str) -> str:
        return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')

    return "{" + ",".join(f'{k}="{esc(v)}"' for k, v in sorted(labels.items())) + "}"


def _seconds(stamp: str | None) -> float | None:
    return datetime.fromisoformat(stamp).timestamp() if stamp else None


def render_metrics(outcome: RunOutcome, records: list[ResultRecord], prefix: str = "va") -> str:
    base = {"tool": outcome.tool, "group": outcome.group or ""}
    lines: list[str] = []

    def metric(name: str, kind: str, help_: str, samples: list[tuple[dict, float]]) -> None:
        lines.append(f"# HELP {prefix}_{name} {help_}")
        lines.append(f"# TYPE {prefix}_{name} {kind}")
        lines.extend(
            f"{prefix}_{name}{_labels(**base, **extra)} {value:g}" for extra, value in samples
        )

    metric(
        "run_exit_code",
        "gauge",
        "Exit code of the last run.",
        [({}, outcome.exit_code if outcome.exit_code is not None else -1)],
    )
    finished, started = _seconds(outcome.finished), _seconds(outcome.started)
    if finished is not None:
        metric("run_timestamp_seconds", "gauge", "When the last run finished.", [({}, finished)])
        if started is not None:
            metric(
                "run_duration_seconds",
                "gauge",
                "Duration of the last run.",
                [({}, finished - started)],
            )
    cases = Counter((r.target, r.verdict.value) for r in records)
    metric(
        "cases",
        "gauge",
        "Cases of the last run by target and verdict.",
        [({"target": t, "verdict": v}, n) for (t, v), n in sorted(cases.items())],
    )
    metric(
        "skips",
        "gauge",
        "Skipped cases of the last run by skip class.",
        [({"skip_class": k}, n) for k, n in sorted(outcome.skips.items())],
    )
    metric(
        "run_published",
        "gauge",
        "1 if the last run was published or not asked to be.",
        [({}, 1 if outcome.publication.delivered else 0)],
    )
    return "\n".join(lines) + "\n"


def write_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(text)
    os.chmod(temp, 0o644)
    os.replace(temp, path)


class MetricsFile(RunHooks):
    """Writes metrics.prom into the run directory (published with it) and atomically to
    `path` (e.g. the textfile collector directory), rewritten once publication is known."""

    def __init__(self, path: Path | None = None, prefix: str = "va"):
        self.path = path
        self.prefix = prefix
        self._outcome: RunOutcome | None = None

    def on_finish(self, run: RunDir, outcome: RunOutcome) -> None:
        self._outcome = outcome
        text = render_metrics(outcome, run.results(), self.prefix)
        run.write_text(RunFile.METRICS, text)
        if self.path:
            write_atomically(self.path, text)

    def on_published(self, run: RunDir, publication: Publication) -> None:
        # The session has already set outcome.publication; only the external file changes.
        if self.path and self._outcome is not None:
            write_atomically(self.path, render_metrics(self._outcome, run.results(), self.prefix))
