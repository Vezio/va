"""File names and schema identifiers used by a run.

Writers and readers both use these constants, so a name is defined in a single place and a
reader cannot refer to a file that no writer produces. Schemas are versioned. Adding a field is
a compatible change, while renaming or removing a field requires a new version, because published
runs remain in use after the code that wrote them has changed.
"""

from __future__ import annotations

from enum import StrEnum


class RunFile(StrEnum):
    """Files and directories inside a run directory."""

    RESULTS = "results.jsonl"  # One ResultRecord per line.
    OUTCOME = "outcome.json"  # The RunOutcome.
    SUMMARY = "summary.md"  # A readable summary, suitable for $GITHUB_STEP_SUMMARY.
    LOG = "run.log"  # The redacted DEBUG log.
    ENV = "env.json"  # The tool version and the options chosen at start.
    EVIDENCE = "evidence"  # Laid out as evidence/<target>/<case>[<variant>]/...
    COMPARISON = "comparison.json"  # Comparison against earlier published runs.
    COMPARISON_MD = "comparison.md"
    HTML = "report.html"  # Optional.
    JUNIT = "junit.xml"  # Optional.
    METRICS = "metrics.prom"  # Optional.
    LAYERS = "layers.json"  # The layer where each failure starts, for a layered stack.
    MANIFEST = "MANIFEST.json"  # Hashes of all other files; written and published last.


class Schema(StrEnum):
    """Versioned schema identifiers stamped into the files a run writes."""

    RESULTS = "va.results/1"
    OUTCOME = "va.outcome/1"
    MANIFEST = "va.manifest/1"
    COMPARISON = "va.comparison/1"
    REPORT = "va.report/1"
    LAYERS = "va.layers/1"


class ArchiveKey(StrEnum):
    """Keys that a sink writes outside any run."""

    WRITE_CHECK = ".write-check"  # Confirms that a destination is writable before a run.
