"""Exit codes that separate blame.

A CI job, a CronJob or a person reads one number and knows whose problem it is, without
parsing logs. `ExitCode` is an IntEnum, so it is the process exit status as it is, and each
member carries its label, headline and meaning, so every report says the same thing.

Two rules keep the codes honest:

- Never widen FAILURES to cover an environment problem. A dead tunnel reported as a test
  failure teaches people to distrust the suite.
- Never let NOT_DELIVERED mask a verdict. A failed run that also failed to publish exits
  FAILURES: the verification code wins (`combine`).

ExitCode.OK is 0, so it is falsy: compare with `is None`, never with `or`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


@dataclass(frozen=True)
class Status:
    """How a run ended, for people: a label (machine), a headline and a meaning."""

    label: str
    headline: str
    meaning: str


class ExitCode(IntEnum):
    OK = 0, "ok", "PASSED", "everything selected passed"
    FAILURES = 1, "failures", "FAILED", "the target failed cases"
    USAGE = 2, "usage", "NOT RUN", "bad usage or an invalid profile"
    PREFLIGHT = 3, "environment", "NOT RUN", "preflight failed, nothing was tested"
    INTERNAL = 4, "internal", "ERROR", "the tool failed internally"
    NOT_DELIVERED = 5, "not_delivered", "NOT DELIVERED", "passed, but results were not delivered"
    INTERRUPTED = 6, "interrupted", "INTERRUPTED", "stopped early; results are partial"

    status: Status

    def __new__(cls, value: int, label: str, headline: str, meaning: str) -> ExitCode:
        member = int.__new__(cls, value)
        member._value_ = value
        member.status = Status(label, headline, meaning)
        return member

    @property
    def label(self) -> str:
        return self.status.label

    @classmethod
    def parse(cls, code: int | None) -> ExitCode | None:
        """The ExitCode for a raw status, or None for one outside the vocabulary (a process
        killed by a signal reports a negative status: it crashed)."""
        return next((member for member in cls if member.value == code), None)

    @classmethod
    def epilog(cls) -> str:
        return "exit codes: " + " | ".join(f"{c.value} {c.label}" for c in cls)


# A status outside the vocabulary: the process died before it could say how it ended.
CRASHED = Status("crashed", "CRASHED", "the run died before recording its outcome")


def status_of(code_or_label: int | str | None) -> Status:
    """The Status for an exit code or a label; CRASHED for anything else."""
    for member in ExitCode:
        if code_or_label == member.label or (
            isinstance(code_or_label, int) and code_or_label == member.value
        ):
            return member.status
    return CRASHED


def label_of(code: int | None) -> str:
    return status_of(code).label


def combine(verification: ExitCode, delivered: bool) -> ExitCode:
    """The process exit code once a run is verified and (maybe) delivered. Delivery can only
    turn OK into NOT_DELIVERED; it never hides any other code."""
    if verification is not ExitCode.OK:
        return verification
    return ExitCode.OK if delivered else ExitCode.NOT_DELIVERED
