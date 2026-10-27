"""Compare a run with earlier ones: four transitions, each with its own reading.

Keyed by CaseKey (target, case, variant). Only pass, fail and error are verdicts; a skip is an
absence of evidence.

| Change         | Transition          | Reading                                         |
|----------------|---------------------|-------------------------------------------------|
| REGRESSION     | pass -> fail/error  | the thing to page on                            |
| RECOVERY       | fail/error -> pass  | reported on purpose: silence after a fix erodes trust |
| LOST_EVIDENCE  | verdict -> skip     | not a failure; the skip class says why          |
| NEW_EVIDENCE   | skip -> fail/error  | new evidence, not new breakage                  |
| FLAKY          | the window disagrees| not a page                                      |
| NEW_CASE       | no earlier result   | informational                                   |

Zero cases is never a pass.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from .model import CaseKey, ResultRecord, Verdict, to_jsonable

COMPARISON_SCHEMA = "runkit.comparison/1"


class ChangeKind(StrEnum):
    REGRESSION = "regression"
    RECOVERY = "recovery"
    FLAKY = "flaky"
    LOST_EVIDENCE = "lost_evidence"
    NEW_EVIDENCE = "new_evidence"
    NEW_CASE = "new_case"


TITLES = MappingProxyType(
    {
        ChangeKind.REGRESSION: "Regressions",
        ChangeKind.RECOVERY: "Recoveries",
        ChangeKind.FLAKY: "Flaky (the window disagrees)",
        ChangeKind.LOST_EVIDENCE: "Lost evidence (now skipped)",
        ChangeKind.NEW_EVIDENCE: "New evidence (was skipped, now failing)",
        ChangeKind.NEW_CASE: "New cases",
    }
)


@dataclass(frozen=True)
class Change:
    kind: ChangeKind
    key: CaseKey
    before: tuple[str, ...]  # the window's verdicts, oldest first
    after: str
    skip_class: str | None = None

    def describe(self) -> str:
        was = "/".join(self.before) or "none"
        tail = f" ({self.skip_class})" if self.skip_class else ""
        return f"{self.key}: {was} -> {self.after}{tail}"


@dataclass
class Comparison:
    current_run: str
    baseline_runs: list[str]
    changes: list[Change] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    schema: str = COMPARISON_SCHEMA

    def of(self, kind: ChangeKind) -> list[Change]:
        return [c for c in self.changes if c.kind is kind]

    @property
    def regressions(self) -> list[Change]:
        return self.of(ChangeKind.REGRESSION)

    @property
    def regressed(self) -> bool:
        return bool(self.regressions)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self) | {"regressed": self.regressed}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Comparison:
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
        lines = [f"## Comparison with {', '.join(self.baseline_runs) or 'nothing'}", ""]
        lines += [f"- problem: {p}" for p in self.problems]
        for kind, title in TITLES.items():
            items = self.of(kind)
            if items:
                lines += [f"### {title} ({len(items)})", ""]
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
    """`baselines` are (run id, records), oldest first."""
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
