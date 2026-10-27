"""The data a run produces, as typed records that round-trip through JSON.

Every file a run writes is a serialised form of something here: `ResultRecord` lines in
`results.jsonl`, a `RunOutcome` in `outcome.json`. Readers (comparison, reports, notifiers,
dashboards) use `from_dict` and get the same types back, so a field is spelled once.

Schemas are versioned (`schema`). Adding a field is compatible; renaming or removing one
needs a new version, because published runs outlive the code that wrote them.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any

from . import __version__
from .exit_codes import ExitCode, label_of

RESULTS_SCHEMA = "runkit.results/1"
OUTCOME_SCHEMA = "runkit.outcome/1"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def to_jsonable(value: Any) -> Any:
    """Dataclasses, enums and tuples as plain JSON values."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | frozenset | set):
        return [to_jsonable(v) for v in value]
    return value


def _known(cls, data: Mapping[str, Any]) -> dict[str, Any]:
    """Only the fields `cls` declares: a reader ignores fields a newer writer added."""
    names = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in names}


# ------------------------------------------------------------------ verdicts and skips


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"  # the target did the wrong thing
    ERROR = "error"  # the case could not reach a verdict
    SKIP = "skip"  # not run; the skip class says what is missing

    @property
    def is_verdict(self) -> bool:
        """Whether a comparison reads it: a skip is an absence of evidence."""
        return self is not Verdict.SKIP

    @property
    def is_failure(self) -> bool:
        return self in (Verdict.FAIL, Verdict.ERROR)


@dataclass(frozen=True)
class SkipClass:
    name: str
    meaning: str


class SkipClasses(Mapping[str, SkipClass]):
    """An app's skip vocabulary: immutable, extended by building a new one. Each class names a
    different missing thing with a different fix, so classes are never merged or reused."""

    def __init__(self, classes: Iterable[SkipClass]):
        items: dict[str, SkipClass] = {}
        for c in classes:
            if c.name in items:
                raise ValueError(f"skip class {c.name!r} is defined twice")
            items[c.name] = c
        self._items = items

    def extend(self, *classes: SkipClass) -> SkipClasses:
        return SkipClasses([*self._items.values(), *classes])

    def __getitem__(self, name: str) -> SkipClass:
        return self._items[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:  # immutable, so safe as a dataclass default
        return hash(tuple(self._items.values()))


UNDECLARED = "undeclared"

DEFAULT_SKIP_CLASSES: SkipClasses = SkipClasses(
    [
        SkipClass(UNDECLARED, "the profile does not declare what the case needs (a target gap)"),
        SkipClass("not_implemented", "the tool cannot test this yet (a tool gap)"),
        SkipClass("requires_instrumentation", "needs an instrument this run does not have"),
        SkipClass("excluded_by_policy", "deliberately not run here, e.g. on a shared system"),
        SkipClass("throttled", "our own quota or rate limit stopped it"),
    ]
)


# ------------------------------------------------------------------ one case's result


@dataclass(frozen=True)
class Check:
    """One observation a case recorded before it decided."""

    name: str
    observed: Any
    expected: Any
    ok: bool

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Check:
        return cls(**_known(cls, data))


@dataclass(frozen=True)
class CaseKey:
    """What identifies a case across runs: where, what, and which parametrisation."""

    target: str
    case_id: str
    variant: str = ""

    def __str__(self) -> str:
        return f"{self.target} {self.case_id}" + (f"[{self.variant}]" if self.variant else "")


@dataclass
class ResultRecord:
    run_id: str
    case_id: str
    verdict: Verdict
    target: str = ""
    variant: str = ""
    schema: str = RESULTS_SCHEMA
    ts: str = field(default_factory=now)
    tool_version: str = __version__
    duration_s: float = 0.0
    reason: str = ""
    skip_class: str | None = None
    checks: list[Check] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)  # app-specific fields

    def __post_init__(self) -> None:
        self.verdict = Verdict(self.verdict)
        if (self.verdict is Verdict.SKIP) != (self.skip_class is not None):
            raise ValueError("a skip carries a skip_class, and only a skip does")

    @property
    def key(self) -> CaseKey:
        return CaseKey(self.target, self.case_id, self.variant)

    @property
    def failed_check(self) -> Check | None:
        return next((c for c in self.checks if not c.ok), None)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResultRecord:
        values = _known(cls, data)
        values["checks"] = [Check.from_dict(c) for c in data.get("checks") or []]
        return cls(**values)


# ------------------------------------------------------------------ the run's outcome


@dataclass(frozen=True)
class Counts:
    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errored + self.skipped

    @classmethod
    def of(cls, records: Iterable[ResultRecord]) -> Counts:
        c = Counter(r.verdict for r in records)
        return cls(c[Verdict.PASS], c[Verdict.FAIL], c[Verdict.ERROR], c[Verdict.SKIP])


@dataclass(frozen=True)
class Failure:
    key: CaseKey
    verdict: Verdict
    check: str | None
    reason: str
    # A failure whose recorded checks all passed contradicts its own evidence.
    unexplained: bool = False

    @classmethod
    def of(cls, record: ResultRecord, max_reason: int = 300) -> Failure:
        first = record.failed_check
        return cls(
            record.key,
            record.verdict,
            first.name if first else None,
            record.reason[:max_reason],
            record.verdict is Verdict.FAIL and first is None,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Failure:
        values = _known(cls, data)
        values["key"] = CaseKey(**data["key"])
        values["verdict"] = Verdict(data["verdict"])
        return cls(**values)


class Severity(StrEnum):
    ERROR = "error"  # blocks the run: exit 3
    WARNING = "warning"  # limits the evidence; the run goes on


@dataclass(frozen=True)
class Problem:
    """A preflight finding. `kind` names whose fix it is: credentials, reachability, output..."""

    kind: str
    message: str
    severity: Severity = Severity.ERROR


class PublicationStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    PUBLISHED = "published"
    SKIPPED = "skipped"  # the destination is unusable here (no credentials, no plugin)
    FAILED = "failed"


@dataclass(frozen=True)
class Publication:
    status: PublicationStatus = PublicationStatus.NOT_REQUESTED
    destination: str | None = None
    location: str | None = None
    reason: str | None = None

    @property
    def delivered(self) -> bool:
        """Whether delivery succeeded, or was not asked for."""
        return self.status in (PublicationStatus.PUBLISHED, PublicationStatus.NOT_REQUESTED)


@dataclass
class RunOutcome:
    """How a run ended. Verification (exit code, counts, failures) and publication are kept
    apart: the published copy cannot know how its own upload went, so `publication` is only
    filled on the local copy, afterwards."""

    run_id: str
    tool: str
    tool_version: str = __version__
    schema: str = OUTCOME_SCHEMA
    started: str = field(default_factory=now)
    finished: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    subject: dict[str, Any] = field(default_factory=dict)  # what was tested: profile, digest
    group: str | None = None  # the archive partition it is published under
    links: dict[str, str] = field(default_factory=dict)  # name -> URL, e.g. the CI run
    exit_code: int | None = None
    exit_label: str | None = None
    counts: Counts = field(default_factory=Counts)
    skips: dict[str, int] = field(default_factory=dict)
    failures: list[Failure] = field(default_factory=list)
    preflight: list[Problem] = field(default_factory=list)
    publication: Publication = field(default_factory=Publication)
    extra: dict[str, Any] = field(default_factory=dict)

    def record(self, records: list[ResultRecord], max_failures: int = 50) -> None:
        self.counts = Counts.of(records)
        self.skips = dict(sorted(Counter(r.skip_class for r in records if r.skip_class).items()))
        self.failures = [Failure.of(r) for r in records if r.verdict.is_failure][:max_failures]

    def finish(self, code: int, label: str | None = None) -> None:
        self.finished = now()
        self.exit_code = int(code)
        self.exit_label = label or label_of(code)

    @property
    def exit(self) -> ExitCode | None:
        return ExitCode.parse(self.exit_code)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunOutcome:
        values = _known(cls, data)
        values["counts"] = Counts(**_known(Counts, data.get("counts") or {}))
        values["failures"] = [Failure.from_dict(f) for f in data.get("failures") or []]
        values["preflight"] = [
            Problem(p["kind"], p["message"], Severity(p.get("severity", "error")))
            for p in data.get("preflight") or []
        ]
        pub = data.get("publication") or {}
        values["publication"] = Publication(
            PublicationStatus(pub.get("status", "not_requested")),
            pub.get("destination"),
            pub.get("location"),
            pub.get("reason"),
        )
        return cls(**values)
