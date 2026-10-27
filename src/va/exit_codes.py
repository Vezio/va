"""Exit codes that indicate which party is responsible for a run's result.

A CI job, a CronJob or an operator can read the exit status and determine whose problem it is
without parsing logs. `ExitCode` is an `IntEnum`, so each member is used directly as the process
exit status. Each member also carries a label, a headline and a meaning, so that all reports
describe the same code in the same words.

Two rules govern the codes:

- FAILURES is used only for failures of the target under test, not for environment problems.
  Reporting an unreachable endpoint as a test failure would make the suite's results
  unreliable.
- NOT_DELIVERED does not replace a verification result. A run that failed and also failed to
  publish exits with FAILURES, because the verification code takes precedence (see `combine`).

`ExitCode.OK` is 0 and therefore falsy, so test for a missing code with `is None` rather than
with `or`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


@dataclass(frozen=True)
class Status:
    """A human-readable description of how a run ended.

    Attributes:
        label: A short machine-readable identifier.
        headline: A short uppercase heading for reports.
        meaning: A one-line explanation.
    """

    label: str
    headline: str
    meaning: str


class ExitCode(IntEnum):
    """The process exit codes, each with its `Status`.

    Attributes:
        OK: All selected cases passed.
        FAILURES: The target failed one or more cases.
        USAGE: The command line or the profile is invalid.
        PREFLIGHT: The preflight checks failed and nothing was tested.
        INTERNAL: The tool itself failed.
        NOT_DELIVERED: Verification passed, but the results were not delivered.
        INTERRUPTED: The run was stopped early and its results are partial.
        status: The label, headline and meaning of the code.
    """

    OK = 0, "ok", "PASSED", "everything selected passed"
    FAILURES = 1, "failures", "FAILED", "the target failed cases"
    USAGE = 2, "usage", "NOT RUN", "bad usage or an invalid profile"
    PREFLIGHT = 3, "environment", "NOT RUN", "preflight failed, nothing was tested"
    INTERNAL = 4, "internal", "ERROR", "the tool failed internally"
    NOT_DELIVERED = 5, "not_delivered", "NOT DELIVERED", "passed, but results were not delivered"
    INTERRUPTED = 6, "interrupted", "INTERRUPTED", "stopped early; results are partial"

    status: Status

    def __new__(cls, value: int, label: str, headline: str, meaning: str) -> ExitCode:
        """Create a member with its integer value and attach its `Status`."""
        member = int.__new__(cls, value)
        member._value_ = value
        member.status = Status(label, headline, meaning)
        return member

    @property
    def label(self) -> str:
        """The machine-readable label of the code."""
        return self.status.label

    @classmethod
    def parse(cls, code: int | None) -> ExitCode | None:
        """Return the `ExitCode` for a raw exit status.

        A process killed by a signal reports a negative status, which is not a member and is
        therefore treated as a crash by callers.

        Args:
            code: The raw exit status, or None.

        Returns:
            The matching member, or None if `code` is not a known exit code.
        """
        return next((member for member in cls if member.value == code), None)

    @classmethod
    def epilog(cls) -> str:
        """Return a one-line list of all exit codes for command-line help.

        Returns:
            A string such as `exit codes: 0 ok | 1 failures | ...`.
        """
        return "exit codes: " + " | ".join(f"{c.value} {c.label}" for c in cls)


# The status used when a process ended before recording an outcome, so its code is unknown.
CRASHED = Status("crashed", "CRASHED", "the run died before recording its outcome")


def status_of(code_or_label: int | str | None) -> Status:
    """Return the `Status` for an exit code or a label.

    Args:
        code_or_label: A numeric exit code, a label, or None.

    Returns:
        The matching status, or `CRASHED` if there is no match.
    """
    for member in ExitCode:
        if code_or_label == member.label or (
            isinstance(code_or_label, int) and code_or_label == member.value
        ):
            return member.status
    return CRASHED


def label_of(code: int | None) -> str:
    """Return the label for an exit code.

    Args:
        code: The numeric exit code, or None.

    Returns:
        The label of the code, or `crashed` if the code is unknown.
    """
    return status_of(code).label


def combine(verification: ExitCode, delivered: bool) -> ExitCode:
    """Return the process exit code after verification and delivery.

    Delivery can only change OK into NOT_DELIVERED. Any other verification code is returned
    unchanged.

    Args:
        verification: The exit code determined by verification.
        delivered: Whether the results were delivered or delivery was not requested.

    Returns:
        The exit code for the process.
    """
    if verification is not ExitCode.OK:
        return verification
    return ExitCode.OK if delivered else ExitCode.NOT_DELIVERED
