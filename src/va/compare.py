"""Comparison of a run with earlier runs.

Results are matched by `CaseKey` (target, case and variant). Only pass, fail and error are
verdicts. A skip provides no evidence about the target and is treated as an absence of a verdict.

| Change        | Transition            | Interpretation                                  |
|---------------|-----------------------|-------------------------------------------------|
| REGRESSION    | pass -> fail/error    | Requires attention.                             |
| RECOVERY      | fail/error -> pass    | Reported so that a fix is visible.              |
| LOST_EVIDENCE | verdict -> skip       | Not a failure; the skip class gives the cause.  |
| NEW_EVIDENCE  | skip -> fail/error    | Newly observed, not necessarily newly broken.   |
| FLAKY         | earlier runs disagree | Inconsistent results; not a regression.         |
| NEW_CASE      | no earlier result     | Informational.                                  |

A run that recorded no cases is reported as a problem and is not treated as a pass.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .layout import Schema
from .model import CaseKey, ResultRecord, Verdict, to_jsonable


class ChangeKind(StrEnum):
    """The kind of change between a case's earlier results and its current result.

    Attributes:
        REGRESSION: The case passed in all earlier runs and now fails or errors.
        RECOVERY: The case failed or errored in the latest earlier run and now passes.
        FLAKY: The case now fails, and earlier runs contain both passes and failures.
        LOST_EVIDENCE: The case had a verdict in the latest earlier run and is now skipped.
        NEW_EVIDENCE: The case was skipped in the latest earlier run and now fails or errors.
        NEW_CASE: The case has no result in any earlier run.
    """

    REGRESSION = "regression"
    RECOVERY = "recovery"
    FLAKY = "flaky"
    LOST_EVIDENCE = "lost_evidence"
    NEW_EVIDENCE = "new_evidence"
    NEW_CASE = "new_case"

    @property
    def heading(self) -> str:
        """The heading a report uses for changes of this kind."""
        return {
            "regression": "Regressions",
            "recovery": "Recoveries",
            "flaky": "Flaky (the window disagrees)",
            "lost_evidence": "Lost evidence (now skipped)",
            "new_evidence": "New evidence (was skipped, now failing)",
            "new_case": "New cases",
        }[self.value]


@dataclass(frozen=True)
class Change:
    """A change in a single case's result.

    Attributes:
        kind: The kind of change.
        key: The key of the case.
        before: The case's verdicts in the earlier runs, oldest first.
        after: The case's verdict in the current run.
        skip_class: The skip class of the current result, if it is a skip.
    """

    kind: ChangeKind
    key: CaseKey
    before: tuple[str, ...]
    after: str
    skip_class: str | None = None

    def describe(self) -> str:
        """Return a one-line description such as `target case: pass -> fail`.

        Returns:
            The description.
        """
        was = "/".join(self.before) or "none"
        tail = f" ({self.skip_class})" if self.skip_class else ""
        return f"{self.key}: {was} -> {self.after}{tail}"


@dataclass
class Comparison:
    """The result of comparing a run with earlier runs.

    Attributes:
        current_run: The identifier of the run being compared.
        baseline_runs: The identifiers of the earlier runs, oldest first.
        changes: The changes found, in the order of the current run's results.
        problems: Conditions that limit the comparison, such as a run with no cases.
        schema: The schema version of the comparison.
    """

    current_run: str
    baseline_runs: list[str]
    changes: list[Change] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    schema: str = Schema.COMPARISON.value

    def of(self, kind: ChangeKind) -> list[Change]:
        """Return the changes of one kind.

        Args:
            kind: The kind of change to select.

        Returns:
            The matching changes.
        """
        return [c for c in self.changes if c.kind is kind]

    @property
    def regressions(self) -> list[Change]:
        """The changes that are regressions."""
        return self.of(ChangeKind.REGRESSION)

    @property
    def regressed(self) -> bool:
        """Whether the comparison found at least one regression."""
        return bool(self.regressions)

    def to_dict(self) -> dict[str, Any]:
        """Return the comparison as a JSON-compatible dict, including a `regressed` flag.

        Returns:
            The serialised comparison.
        """
        return to_jsonable(self) | {"regressed": self.regressed}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Comparison:
        """Build a comparison from its serialised form.

        Args:
            data: The serialised comparison.

        Returns:
            The comparison.
        """
        changes = [
            Change(
                ChangeKind(c["kind"]),
                CaseKey(**c["key"]),
                tuple(c["before"]),
                c["after"],
                c.get("skip_class"),
            )
            for c in data.get("changes", [])
        ]
        return cls(
            data.get("current_run", ""),
            list(data.get("baseline_runs", [])),
            changes,
            list(data.get("problems", [])),
        )

    def markdown(self) -> str:
        """Render the comparison as Markdown, grouped by kind of change.

        Returns:
            The Markdown text, ending with a newline.
        """
        lines = [f"## Comparison with {', '.join(self.baseline_runs) or 'nothing'}", ""]
        lines += [f"- problem: {p}" for p in self.problems]
        for kind in ChangeKind:
            items = self.of(kind)
            if items:
                lines += [f"### {kind.heading} ({len(items)})", ""]
                lines += [f"- {c.describe()}" for c in items]
                lines.append("")
        if not self.changes and not self.problems:
            lines.append("No change.")
        return "\n".join(lines) + "\n"


def compare(
    current: Sequence[ResultRecord],
    baselines: Sequence[tuple[str, Sequence[ResultRecord]]],
    current_run: str = "",
) -> Comparison:
    """Compare a run's results with those of earlier runs.

    Args:
        current: The result records of the current run.
        baselines: Pairs of run identifier and result records for the earlier runs, oldest
            first.
        current_run: The identifier of the current run.

    Returns:
        The comparison. If the current run has no results or there are no earlier runs, the
        comparison contains a problem and no changes.
    """
    result = Comparison(current_run, [run for run, _ in baselines])
    if not current:
        result.problems.append("the current run recorded no cases: never read as a pass")
        return result
    if not baselines:
        result.problems.append("no earlier run to compare with")
        return result
    result.problems += [f"baseline {run} recorded no cases" for run, recs in baselines if not recs]
    windows = [{r.key: r.verdict for r in records} for _, records in baselines]

    for record in current:
        before = tuple(w[record.key] for w in windows if record.key in w)
        now = record.verdict
        kind = _classify(before, now)
        if kind is not None:
            result.changes.append(
                Change(
                    kind, record.key, tuple(v.value for v in before), now.value, record.skip_class
                )
            )
    return result


def _classify(before: tuple[Verdict, ...], now: Verdict) -> ChangeKind | None:
    """Classify the change from a case's earlier results to its current result.

    Args:
        before: The case's results in the earlier runs, oldest first.
        now: The case's result in the current run.

    Returns:
        The kind of change, or None if the result is unchanged or the transition is not
        reported.
    """
    if not before:
        return ChangeKind.NEW_CASE
    latest = before[-1]
    verdicts = [v for v in before if v.is_verdict]
    if now.is_failure and latest.is_verdict:
        if all(v is Verdict.PASS for v in verdicts):
            return ChangeKind.REGRESSION
        if Verdict.PASS in verdicts:
            return ChangeKind.FLAKY
        return None
    if now is Verdict.PASS and latest.is_failure:
        return ChangeKind.RECOVERY
    if now is Verdict.SKIP and latest.is_verdict:
        return ChangeKind.LOST_EVIDENCE
    if now.is_failure and latest is Verdict.SKIP:
        return ChangeKind.NEW_EVIDENCE
    return None
