"""A description of a finished run that is independent of the output channel.

A `RunReport` is built from a `RunOutcome` and, when available, a `Comparison`. The run's summary
file, the terminal output and all notifiers render the same report, so a Slack message, a webhook
payload and `summary.md` describe the run consistently.
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
    """A condensed form of a `Comparison` for inclusion in a report.

    Attributes:
        baseline_runs: The identifiers of the runs compared against.
        counts: The number of changes per `ChangeKind` value, for kinds that occurred.
        regressions: A human-readable description of each regression.
        problems: Problems encountered while comparing.
    """

    baseline_runs: list[str]
    counts: dict[str, int]
    regressions: list[str]
    problems: list[str]

    @classmethod
    def of(cls, comparison: Comparison) -> ComparisonSummary:
        """Summarise a comparison.

        Args:
            comparison: The comparison to summarise.

        Returns:
            The summary.
        """
        return cls(
            list(comparison.baseline_runs),
            {k.value: len(comparison.of(k)) for k in ChangeKind if comparison.of(k)},
            [c.describe() for c in comparison.regressions],
            list(comparison.problems),
        )


@dataclass(frozen=True)
class RunReport:
    """A report on a finished run, rendered by summaries, the terminal and notifiers.

    Attributes:
        tool: The name of the tool that performed the run.
        run_id: The run identifier.
        subject: A human-readable name for what was tested.
        headline: The headline of the exit status, for example `PASSED` or `FAILED`.
        meaning: A one-line explanation of the exit status.
        exit_code: The numeric exit code, or None if the run did not record one.
        exit_label: The label of the exit status.
        outcome: The run outcome the report is based on.
        comparison: A summary of the comparison with earlier runs, if one was made.
        links: Related URLs by name, for example `ci_run`.
        schema: The schema version of the report.
    """

    tool: str
    run_id: str
    subject: str
    headline: str
    meaning: str
    exit_code: int | None
    exit_label: str
    outcome: RunOutcome
    comparison: ComparisonSummary | None = None
    links: dict[str, str] = field(default_factory=dict)
    schema: str = Schema.REPORT.value

    @classmethod
    def build(
        cls,
        outcome: RunOutcome,
        comparison: Comparison | None = None,
        links: dict[str, str] | None = None,
    ) -> RunReport:
        """Build a report from a run outcome.

        The links of the outcome are merged with `links`, which take precedence. If the run was
        published, its location is added as the `results` link unless one is already present.

        Args:
            outcome: The run outcome.
            comparison: The comparison with earlier runs, if any.
            links: Additional related URLs by name.

        Returns:
            The report.
        """
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
        """Whether the run exited with `ExitCode.OK`."""
        return self.exit_code == ExitCode.OK

    @property
    def failures(self) -> list[Failure]:
        """The failures recorded in the outcome."""
        return self.outcome.failures

    @property
    def publication(self) -> Publication:
        """The publication result recorded in the outcome."""
        return self.outcome.publication

    @property
    def preflight(self) -> list[Problem]:
        """The preflight problems recorded in the outcome."""
        return self.outcome.preflight

    def title(self) -> str:
        """Return a one-line title with the tool, subject, headline and exit code.

        Returns:
            The title.
        """
        return f"{self.tool} {self.subject}: {self.headline} (exit {self.exit_code})"

    def counts_line(self) -> str:
        """Return a one-line summary of the verdict counts and skips by class.

        Returns:
            The summary line.
        """
        c = self.outcome.counts
        line = f"{c.passed} passed, {c.failed} failed, {c.errored} errors, {c.skipped} skipped"
        if self.outcome.skips:
            line += " (" + ", ".join(f"{n} {k}" for k, n in self.outcome.skips.items()) + ")"
        return line

    def to_dict(self) -> dict[str, Any]:
        """Return the report as a JSON-compatible dict.

        Returns:
            The serialised report.
        """
        return to_jsonable(self)

    def markdown(self, max_failures: int = DEFAULTS.max_failures) -> str:
        """Render the report as Markdown for `summary.md` and `$GITHUB_STEP_SUMMARY`.

        Args:
            max_failures: The maximum number of failures to list.

        Returns:
            The Markdown text, ending with a newline.
        """
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
