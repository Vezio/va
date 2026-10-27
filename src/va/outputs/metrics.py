"""Rendering of a Prometheus text-format metrics file for each run.

When the file is written to node_exporter's textfile collector directory, or read by another
scraper that understands the format, run health appears on the same dashboards and alerts as
the infrastructure. The metrics are:

    va_run_exit_code{tool,group}             the last run's exit code
    va_run_timestamp_seconds{tool,group}     when it finished
    va_run_duration_seconds{tool,group}
    va_cases{tool,group,target,verdict}      cases by verdict
    va_skips{tool,group,skip_class}          skips by class
    va_run_published{tool,group}             1 if published (or not asked)

The metric prefix is configurable, and an application usually passes its own name.

The external file is written atomically, by renaming a temporary file into place, so that a
scraper does not read a partially written file. When the run is published, the file is
rewritten afterwards so that the publication metric is accurate.
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
    """Format labels as a Prometheus label set, sorted by name.

    Args:
        **labels: The label names and values.

    Returns:
        The label set, including braces, with values escaped.
    """

    def esc(value: str) -> str:
        """Escape backslashes, newlines and double quotes in a label value.

        Args:
            value: The raw label value.

        Returns:
            The escaped value.
        """
        return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')

    return "{" + ",".join(f'{k}="{esc(v)}"' for k, v in sorted(labels.items())) + "}"


def _seconds(stamp: str | None) -> float | None:
    """Convert an ISO 8601 timestamp to seconds since the epoch.

    Args:
        stamp: The timestamp, or None.

    Returns:
        The POSIX timestamp, or None when no timestamp was given.
    """
    return datetime.fromisoformat(stamp).timestamp() if stamp else None


def render_metrics(outcome: RunOutcome, records: list[ResultRecord], prefix: str = "va") -> str:
    """Render a run's metrics in the Prometheus text exposition format.

    Every sample carries the `tool` and `group` labels. The timestamp and duration metrics are
    included only when the corresponding times are known.

    Args:
        outcome: The run outcome.
        records: The run's result records.
        prefix: The prefix for every metric name.

    Returns:
        The metrics text, ending with a newline.
    """
    base = {"tool": outcome.tool, "group": outcome.group or ""}
    lines: list[str] = []

    def metric(name: str, kind: str, help_: str, samples: list[tuple[dict, float]]) -> None:
        """Append one metric family with its HELP and TYPE lines and its samples.

        Args:
            name: The metric name without the prefix.
            kind: The Prometheus metric type.
            help_: The help text.
            samples: Pairs of extra labels and sample value.
        """
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
    """Write text to a file by renaming a temporary file into place.

    The temporary file is created in the target directory, so the rename is atomic, and the
    result is made world-readable for the scraper.

    Args:
        path: The destination file. Parent directories are created.
        text: The content to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(text)
    os.chmod(temp, 0o644)
    os.replace(temp, path)


class MetricsFile(RunHooks):
    """A run hook that writes metrics.prom into the run directory and optionally to a path.

    The file in the run directory is published with the run. The external file, for example
    in a textfile collector directory, is written atomically and rewritten once the
    publication result is known.

    Args:
        path: An optional external path to write the metrics file to.
        prefix: The prefix for every metric name.
    """

    def __init__(self, path: Path | None = None, prefix: str = "va"):
        self.path = path
        self.prefix = prefix
        self._outcome: RunOutcome | None = None

    def on_finish(self, run: RunDir, outcome: RunOutcome) -> None:
        """Render the metrics, write them to the run directory and the external path.

        Args:
            run: The finished run directory.
            outcome: The run outcome, kept for the rewrite after publication.
        """
        self._outcome = outcome
        text = render_metrics(outcome, run.results(), self.prefix)
        run.write_text(RunFile.METRICS, text)
        if self.path:
            write_atomically(self.path, text)

    def on_published(self, run: RunDir, publication: Publication) -> None:
        """Rewrite the external metrics file with the publication result.

        The session has already updated the outcome's publication field, so only the external
        file needs to change.

        Args:
            run: The published run directory.
            publication: The publication result.
        """
        if self.path and self._outcome is not None:
            write_atomically(self.path, render_metrics(self._outcome, run.results(), self.prefix))
