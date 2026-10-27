"""RunReport: what is said about a finished run, independent of where it is said.

A `RunReport` is built from a `RunOutcome` (and a `Comparison`, when there is one). The run's
summary file, the terminal and every notifier render the same report, so a Slack message, a
webhook payload and `summary.md` cannot disagree about what happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .compare import ChangeKind, Comparison
from .defaults import DEFAULTS
from .exit_codes import ExitCode, status_of
from .layout import Schema
from .model import Failure, Problem, Publication, RunOutcome, to_jsonable


@dataclass(frozen=True)
class ComparisonSummary:
    baseline_runs: list[str]
    counts: dict[str, int]  # ChangeKind value -> number
    regressions: list[str]  # described
    problems: list[str]

    @classmethod
    def of(cls, comparison: Comparison) -> ComparisonSummary:
        return cls(
            list(comparison.baseline_runs),
            {k.value: len(comparison.of(k)) for k in ChangeKind if comparison.of(k)},
            [c.describe() for c in comparison.regressions],
            list(comparison.problems),
        )


@dataclass(frozen=True)
class RunReport:
    tool: str
    run_id: str
    subject: str  # what was tested, for humans
    headline: str  # PASSED, FAILED, ...
    meaning: str
    exit_code: int | None
    exit_label: str
    outcome: RunOutcome
    comparison: ComparisonSummary | None = None
    links: dict[str, str] = field(default_factory=dict)  # name -> URL, e.g. ci_run
    schema: str = Schema.REPORT.value

    @classmethod
    def build(
        cls,
        outcome: RunOutcome,
        comparison: Comparison | None = None,
        links: dict[str, str] | None = None,
    ) -> RunReport:
        status = status_of(outcome.exit_label)
        label, headline, meaning = status.label, status.headline, status.meaning
        links = {**outcome.links, **(links or {})}
        if outcome.publication.location:
            links.setdefault("results", outcome.publication.location)
        return cls(
            tool=outcome.tool,
            run_id=outcome.run_id,
            subject=str(outcome.subject.get("name") or outcome.run_id),
            headline=headline,
            meaning=meaning,
            exit_code=outcome.exit_code,
            exit_label=label,
            outcome=outcome,
            comparison=ComparisonSummary.of(comparison) if comparison else None,
            links=links,
        )

    @property
    def ok(self) -> bool:
        return self.exit_code == ExitCode.OK

    @property
    def failures(self) -> list[Failure]:
        return self.outcome.failures

    @property
    def publication(self) -> Publication:
        return self.outcome.publication

    @property
    def preflight(self) -> list[Problem]:
        return self.outcome.preflight

    def title(self) -> str:
        return f"{self.tool} {self.subject}: {self.headline} (exit {self.exit_code})"

    def counts_line(self) -> str:
        c = self.outcome.counts
        line = f"{c.passed} passed, {c.failed} failed, {c.errored} errors, {c.skipped} skipped"
        if self.outcome.skips:
            line += " (" + ", ".join(f"{n} {k}" for k, n in self.outcome.skips.items()) + ")"
        return line

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def markdown(self, max_failures: int = DEFAULTS.max_failures) -> str:
        """The run's summary.md, fit for $GITHUB_STEP_SUMMARY."""
        o = self.outcome
        lines = [f"## {self.title()}", "", self.meaning + ".", ""]
        if o.labels:
            lines += [", ".join(f"`{k}={v}`" for k, v in o.labels.items()), ""]
        c = o.counts
        lines += [
            "| pass | fail | error | skip |",
            "|---|---|---|---|",
            f"| {c.passed} | {c.failed} | {c.errored} | {c.skipped} |",
            "",
        ]
        if o.skips:
            lines += ["Skips by class: " + ", ".join(f"{k} {v}" for k, v in o.skips.items()), ""]
        if o.preflight:
            lines += ["### Preflight", ""]
            lines += [f"- {p.severity} {p.kind}: {p.message}" for p in o.preflight]
            lines.append("")
        if o.failures:
            lines += ["### Failures", ""]
            for f in o.failures[:max_failures]:
                flag = " (no failed check recorded: the verdict contradicts its evidence)"
                check = f" [{f.check}]" if f.check else ""
                lines.append(f"- `{f.key}`{check}: {f.reason}{flag if f.unexplained else ''}")
            lines.append("")
        if self.comparison:
            lines += [
                f"### Compared with {', '.join(self.comparison.baseline_runs) or 'nothing'}",
                "",
            ]
            counts = ", ".join(f"{n} {k}" for k, n in self.comparison.counts.items())
            lines += [counts or "no change", ""]
            lines += [f"- regression: {r}" for r in self.comparison.regressions]
        for name, url in self.links.items():
            lines.append(f"- {name}: {url}")
        return "\n".join(lines).rstrip() + "\n"
