"""Typed records for the data a run produces, with JSON round-tripping.

Each file a run writes is a serialised form of a type defined here: `ResultRecord` lines in
`results.jsonl` and a `RunOutcome` in `outcome.json`. Readers such as comparison, reports,
notifiers and dashboards use `from_dict` to obtain the same types, so each field is defined in a
single place.

Schemas are versioned through the `schema` field. Adding a field is a compatible change.
Renaming or removing a field requires a new schema version, because published runs remain in use
after the code that wrote them has changed.
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
from .defaults import DEFAULTS
from .exit_codes import ExitCode, label_of
from .layout import Schema


def now() -> str:
    """Return the current UTC time as an ISO 8601 string with second precision.

    Returns:
        The timestamp, for example `2026-01-01T12:00:00+00:00`.
    """
    return datetime.now(UTC).isoformat(timespec="seconds")


def to_jsonable(value: Any) -> Any:
    """Convert dataclasses, enums, mappings and sequences into plain JSON values.

    Conversion is recursive. Enums become their values, mapping keys become strings, and lists,
    tuples and sets become lists. Other values are returned unchanged.

    Args:
        value: The value to convert.

    Returns:
        A structure of dicts, lists and scalars suitable for `json.dumps`.
    """
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
    """Return only the entries of `data` that are fields of the dataclass `cls`.

    This allows a reader to load records written by a newer version that added fields.

    Args:
        cls: The dataclass whose field names are kept.
        data: The serialised record.

    Returns:
        A new dict containing the known fields only.
    """
    names = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in names}


class Verdict(StrEnum):
    """The result of a single case.

    Attributes:
        PASS: The target behaved as expected.
        FAIL: The target behaved incorrectly.
        ERROR: The case could not reach a verdict.
        SKIP: The case was not run. The record's skip class states what was missing.
    """

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIP = "skip"

    @property
    def is_verdict(self) -> bool:
        """Whether comparisons take this result into account; a skip provides no evidence."""
        return self is not Verdict.SKIP

    @property
    def is_failure(self) -> bool:
        """Whether this result is a failure or an error."""
        return self in (Verdict.FAIL, Verdict.ERROR)


@dataclass(frozen=True)
class SkipClass:
    """A named reason for skipping a case.

    Attributes:
        name: The identifier recorded in results.
        meaning: A short human-readable explanation of what was missing.
    """

    name: str
    meaning: str


class SkipClasses(Mapping[str, SkipClass]):
    """An immutable vocabulary of skip classes, keyed by name.

    The vocabulary is extended by building a new instance with `extend`. Each class describes a
    different missing prerequisite with a different remedy, so classes are kept distinct and are
    not merged or reused.

    Args:
        classes: The skip classes to include.

    Raises:
        ValueError: If two classes share a name.
    """

    UNDECLARED = "undeclared"  # Assigned by the runner when the target does not declare a case.

    def __init__(self, classes: Iterable[SkipClass]):
        items: dict[str, SkipClass] = {}
        for c in classes:
            if c.name in items:
                raise ValueError(f"skip class {c.name!r} is defined twice")
            items[c.name] = c
        self._items = items

    @classmethod
    def default(cls) -> SkipClasses:
        """Return the built-in skip vocabulary.

        Returns:
            A vocabulary containing the undeclared, not implemented, instrumentation, policy and
            throttling classes.
        """
        return cls(
            [
                SkipClass(cls.UNDECLARED, "the profile does not declare what the case needs"),
                SkipClass("not_implemented", "the tool cannot test this yet (a tool gap)"),
                SkipClass("requires_instrumentation", "needs an instrument this run lacks"),
                SkipClass("excluded_by_policy", "deliberately not run here, e.g. a shared system"),
                SkipClass("throttled", "our own quota or rate limit stopped it"),
            ]
        )

    def extend(self, *classes: SkipClass) -> SkipClasses:
        """Return a new vocabulary with additional classes.

        Args:
            *classes: The classes to add.

        Returns:
            A new vocabulary containing the existing and the added classes.

        Raises:
            ValueError: If an added class has the same name as an existing one.
        """
        return SkipClasses([*self._items.values(), *classes])

    def __getitem__(self, name: str) -> SkipClass:
        """Return the skip class with the given name."""
        return self._items[name]

    def __iter__(self) -> Iterator[str]:
        """Iterate over the skip class names."""
        return iter(self._items)

    def __len__(self) -> int:
        """Return the number of skip classes."""
        return len(self._items)

    def __hash__(self) -> int:  # The instance is immutable, so it can be a dataclass default.
        """Return a hash of the contained classes."""
        return hash(tuple(self._items.values()))


@dataclass(frozen=True)
class Check:
    """An observation that a case recorded before deciding its verdict.

    Attributes:
        name: The name of the check.
        observed: The value the case observed.
        expected: The value the case expected.
        ok: Whether the observed value satisfied the expectation.
    """

    name: str
    observed: Any
    expected: Any
    ok: bool

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Check:
        """Build a check from its serialised form.

        Args:
            data: The serialised check. Unknown fields are ignored.

        Returns:
            The check.
        """
        return cls(**_known(cls, data))


@dataclass(frozen=True)
class CaseKey:
    """The identity of a case across runs.

    Attributes:
        target: The target the case ran against.
        case_id: The case identifier.
        variant: The parametrisation of the case, or an empty string.
    """

    target: str
    case_id: str
    variant: str = ""

    def __str__(self) -> str:
        """Return the key as `target case_id[variant]`."""
        return f"{self.target} {self.case_id}" + (f"[{self.variant}]" if self.variant else "")


@dataclass
class ResultRecord:
    """The result of one case, stored as one line of `results.jsonl`.

    Attributes:
        run_id: The run that produced the result.
        case_id: The case identifier.
        verdict: The verdict of the case.
        target: The target the case ran against.
        variant: The parametrisation of the case, or an empty string.
        schema: The schema version of the record.
        ts: The time the record was created.
        tool_version: The version of the tool that wrote the record.
        duration_s: The duration of the case in seconds.
        reason: A human-readable explanation of the verdict.
        skip_class: The skip class name. It is set if and only if the verdict is a skip.
        checks: The observations recorded by the case.
        labels: Free-form labels attached to the result.
        extra: Application-specific fields.

    Raises:
        ValueError: If `skip_class` is set for a non-skip verdict, or missing for a skip.
    """

    run_id: str
    case_id: str
    verdict: Verdict
    target: str = ""
    variant: str = ""
    schema: str = Schema.RESULTS.value
    ts: str = field(default_factory=now)
    tool_version: str = __version__
    duration_s: float = 0.0
    reason: str = ""
    skip_class: str | None = None
    checks: list[Check] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalise the verdict and validate the skip class.

        Raises:
            ValueError: If `skip_class` does not match the verdict.
        """
        self.verdict = Verdict(self.verdict)
        if (self.verdict is Verdict.SKIP) != (self.skip_class is not None):
            raise ValueError("a skip carries a skip_class, and only a skip does")

    @property
    def key(self) -> CaseKey:
        """The key identifying this case across runs."""
        return CaseKey(self.target, self.case_id, self.variant)

    @property
    def failed_check(self) -> Check | None:
        """The first check that did not pass, or None if all checks passed."""
        return next((c for c in self.checks if not c.ok), None)

    def to_dict(self) -> dict[str, Any]:
        """Return the record as a JSON-compatible dict.

        Returns:
            The serialised record.
        """
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResultRecord:
        """Build a record from its serialised form.

        Args:
            data: The serialised record. Unknown fields are ignored.

        Returns:
            The record.

        Raises:
            ValueError: If the verdict or skip class is invalid.
        """
        values = _known(cls, data)
        values["checks"] = [Check.from_dict(c) for c in data.get("checks") or []]
        return cls(**values)


@dataclass(frozen=True)
class Counts:
    """The number of results for each verdict.

    Attributes:
        passed: The number of passed cases.
        failed: The number of failed cases.
        errored: The number of cases that ended in an error.
        skipped: The number of skipped cases.
    """

    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        """The total number of cases."""
        return self.passed + self.failed + self.errored + self.skipped

    @classmethod
    def of(cls, records: Iterable[ResultRecord]) -> Counts:
        """Count the verdicts of the given records.

        Args:
            records: The result records to count.

        Returns:
            The counts per verdict.
        """
        c = Counter(r.verdict for r in records)
        return cls(c[Verdict.PASS], c[Verdict.FAIL], c[Verdict.ERROR], c[Verdict.SKIP])


@dataclass(frozen=True)
class Failure:
    """A summary of a failed or errored case, as stored in the run outcome.

    Attributes:
        key: The key of the case.
        verdict: The verdict, either fail or error.
        check: The name of the first failed check, or None if no check failed.
        reason: The reason given by the case, truncated.
        unexplained: Whether the case failed although all of its recorded checks passed, which
            means the recorded evidence does not explain the failure.
    """

    key: CaseKey
    verdict: Verdict
    check: str | None
    reason: str
    unexplained: bool = False

    @classmethod
    def of(cls, record: ResultRecord, max_reason: int = DEFAULTS.max_reason_chars) -> Failure:
        """Summarise a result record as a failure.

        Args:
            record: The failed or errored result record.
            max_reason: The maximum number of characters of the reason to keep.

        Returns:
            The failure summary.
        """
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
        """Build a failure from its serialised form.

        Args:
            data: The serialised failure. Unknown fields are ignored.

        Returns:
            The failure.
        """
        values = _known(cls, data)
        values["key"] = CaseKey(**data["key"])
        values["verdict"] = Verdict(data["verdict"])
        return cls(**values)


class Severity(StrEnum):
    """The severity of a preflight problem.

    Attributes:
        ERROR: The problem prevents the run, which exits with code 3.
        WARNING: The problem limits the evidence collected, but the run continues.
    """

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Problem:
    """A finding from the preflight checks.

    Attributes:
        kind: The area the problem belongs to, which indicates who can fix it, for example
            credentials, reachability or output.
        message: A human-readable description of the problem.
        severity: Whether the problem blocks the run.
    """

    kind: str
    message: str
    severity: Severity = Severity.ERROR


class PublicationStatus(StrEnum):
    """The status of publishing a run's results.

    Attributes:
        NOT_REQUESTED: No publication was requested.
        PUBLISHED: The results were published.
        SKIPPED: The destination cannot be used in this environment, for example because
            credentials or a plugin are missing.
        FAILED: Publication was attempted and failed.
    """

    NOT_REQUESTED = "not_requested"
    PUBLISHED = "published"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class Publication:
    """The result of publishing a run.

    Attributes:
        status: The publication status.
        destination: The configured destination, if any.
        location: Where the results were published, if they were.
        reason: An explanation for a skipped or failed publication.
    """

    status: PublicationStatus = PublicationStatus.NOT_REQUESTED
    destination: str | None = None
    location: str | None = None
    reason: str | None = None

    @property
    def delivered(self) -> bool:
        """Whether delivery succeeded or was not requested."""
        return self.status in (PublicationStatus.PUBLISHED, PublicationStatus.NOT_REQUESTED)


@dataclass
class RunOutcome:
    """The final state of a run, stored in `outcome.json`.

    Verification results (exit code, counts and failures) are kept separate from publication.
    The published copy is written before its upload completes, so `publication` is filled in
    only on the local copy, after publishing.

    Attributes:
        run_id: The run identifier.
        tool: The name of the tool that performed the run.
        tool_version: The version of the tool.
        schema: The schema version of the outcome.
        started: The time the run started.
        finished: The time the run finished, or None while it is in progress.
        labels: Free-form labels attached to the run.
        subject: A description of what was tested, such as the profile and its digest.
        group: The archive partition the run is published under.
        links: Related URLs by name, for example the CI run.
        exit_code: The numeric exit code, or None while the run is in progress.
        exit_label: The label of the exit code.
        counts: The number of results per verdict.
        skips: The number of skipped cases per skip class.
        failures: Summaries of failed and errored cases, up to a limit.
        preflight: The problems found by the preflight checks.
        publication: The result of publishing the run.
        extra: Application-specific fields.
    """

    run_id: str
    tool: str
    tool_version: str = __version__
    schema: str = Schema.OUTCOME.value
    started: str = field(default_factory=now)
    finished: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    subject: dict[str, Any] = field(default_factory=dict)
    group: str | None = None
    links: dict[str, str] = field(default_factory=dict)
    exit_code: int | None = None
    exit_label: str | None = None
    counts: Counts = field(default_factory=Counts)
    skips: dict[str, int] = field(default_factory=dict)
    failures: list[Failure] = field(default_factory=list)
    preflight: list[Problem] = field(default_factory=list)
    publication: Publication = field(default_factory=Publication)
    extra: dict[str, Any] = field(default_factory=dict)

    def record(
        self, records: list[ResultRecord], max_failures: int = DEFAULTS.max_failures
    ) -> None:
        """Update counts, skips and failures from the run's result records.

        Args:
            records: All result records of the run.
            max_failures: The maximum number of failures to keep.
        """
        self.counts = Counts.of(records)
        self.skips = dict(sorted(Counter(r.skip_class for r in records if r.skip_class).items()))
        self.failures = [Failure.of(r) for r in records if r.verdict.is_failure][:max_failures]

    def finish(self, code: int, label: str | None = None) -> None:
        """Mark the run as finished with the given exit code.

        Args:
            code: The exit code.
            label: The exit label. Defaults to the label registered for `code`.
        """
        self.finished = now()
        self.exit_code = int(code)
        self.exit_label = label or label_of(code)

    @property
    def exit(self) -> ExitCode | None:
        """The exit code as an `ExitCode`, or None if it is unset or not a known code."""
        return ExitCode.parse(self.exit_code)

    def to_dict(self) -> dict[str, Any]:
        """Return the outcome as a JSON-compatible dict.

        Returns:
            The serialised outcome.
        """
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunOutcome:
        """Build an outcome from its serialised form.

        Args:
            data: The serialised outcome. Unknown fields are ignored.

        Returns:
            The outcome.
        """
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
