"""Exit codes that separate blame.

A CI job, a CronJob or a person reads one number and knows whose problem it is, without
parsing logs. `ExitCode` is an IntEnum, so it is the process exit status as it is.

Two rules keep the codes honest:

- Never widen FAILURES to cover an environment problem. A dead tunnel reported as a test
  failure teaches people to distrust the suite.
- Never let NOT_DELIVERED mask a verdict. A failed run that also failed to publish exits
  FAILURES: the verification code wins (`combine`).
"""

from __future__ import annotations

from enum import IntEnum
from types import MappingProxyType


class ExitCode(IntEnum):
    OK = 0  # everything selected passed
    FAILURES = 1  # the thing under test failed: the only code that blames the target
    USAGE = 2  # bad arguments or an invalid profile
    PREFLIGHT = 3  # the operator's environment is not ready: nothing was tested
    INTERNAL = 4  # a defect in the tool itself, never reported as a verdict
    NOT_DELIVERED = 5  # the verdict stands, but results or a notification did not arrive
    INTERRUPTED = 6  # SIGINT, SIGTERM or the time budget; what finished is kept

    @property
    def label(self) -> str:
        return _LABELS[self]

    @classmethod
    def parse(cls, code: int | None) -> ExitCode | None:
        """The ExitCode for a raw status, or None for one outside the vocabulary (a process
        killed by a signal reports a negative status: it crashed)."""
        try:
            return cls(code) if code is not None else None
        except ValueError:
            return None


_LABELS = MappingProxyType(
    {
        ExitCode.OK: "ok",
        ExitCode.FAILURES: "failures",
        ExitCode.USAGE: "usage",
        ExitCode.PREFLIGHT: "environment",
        ExitCode.INTERNAL: "internal",
        ExitCode.NOT_DELIVERED: "not_delivered",
        ExitCode.INTERRUPTED: "interrupted",
    }
)

CRASHED = "crashed"  # the label of a status outside the vocabulary

EPILOG = "exit codes: " + " | ".join(f"{c.value} {c.label}" for c in ExitCode)


def label_of(code: int | None) -> str:
    parsed = ExitCode.parse(code)
    return parsed.label if parsed is not None else CRASHED


def combine(verification: ExitCode, delivered: bool) -> ExitCode:
    """The process exit code once a run is verified and (maybe) delivered. Delivery can only
    turn OK into NOT_DELIVERED; it never hides any other code."""
    if verification is not ExitCode.OK:
        return verification
    return ExitCode.OK if delivered else ExitCode.NOT_DELIVERED
