"""Every name a run writes, and every schema id it stamps, in one place.

Writers and readers agree on these, so a rename is one line and a reader never spells a name a
writer does not. Schemas are versioned: adding a field is compatible, renaming or removing one
needs a new version, because published runs outlive the code that wrote them.
"""

from __future__ import annotations

from enum import StrEnum


class RunFile(StrEnum):
    """Files and folders inside a run directory."""

    RESULTS = "results.jsonl"  # one ResultRecord per line
    OUTCOME = "outcome.json"  # the RunOutcome
    SUMMARY = "summary.md"  # a readable summary, fit for $GITHUB_STEP_SUMMARY
    LOG = "run.log"  # the DEBUG log, redacted
    ENV = "env.json"  # tool version and the choices made at start
    EVIDENCE = "evidence"  # evidence/<target>/<case>[<variant>]/...
    COMPARISON = "comparison.json"  # against earlier published runs
    COMPARISON_MD = "comparison.md"
    HTML = "report.html"  # opt-in
    JUNIT = "junit.xml"  # opt-in
    METRICS = "metrics.prom"  # opt-in
    LAYERS = "layers.json"  # where each failure starts, for a stack declared as layers
    MANIFEST = "MANIFEST.json"  # every other file hashed; written and published last


class Schema(StrEnum):
    RESULTS = "va.results/1"
    OUTCOME = "va.outcome/1"
    MANIFEST = "va.manifest/1"
    COMPARISON = "va.comparison/1"
    REPORT = "va.report/1"
    LAYERS = "va.layers/1"


class ArchiveKey(StrEnum):
    """Keys a sink writes outside any run."""

    WRITE_CHECK = ".write-check"  # proves a destination is writable, before a run
