"""Check journal that records each observation before the outcome is decided.

Every assertion records its observation before it decides, for passes as well as failures. A
failure therefore shows the value that caused it, and a pass shows what was checked. `require`
and `fail` record a failed check and raise in a single step, so the deciding check is always
present in the journal.

Example:
    ctx.check("latency_ms", 120, "<= 500", True)        # an observation that does not decide
    ctx.require("http_status", response.status, 200)    # record, then fail unless equal
    ctx.fail("body_parses", error, "a JSON document")   # record a failure and stop
"""

from __future__ import annotations

from typing import Any

from .defaults import DEFAULTS
from .model import Check
from .redact import REDACTOR, Redactor


class CheckFailed(AssertionError):
    """Exception raised when a recorded check fails.

    The runner converts this exception into a `fail` verdict.

    Args:
        check: The failed check.

    Attributes:
        check: The failed check, as recorded in the journal.
    """

    def __init__(self, check: Check):
        super().__init__(f"{check.name}: expected {check.expected!r}, got {check.observed!r}")
        self.check = check


class Journal:
    """Ordered record of the checks made by one case.

    Args:
        redactor: Redactor applied to string values before they are recorded.
        max_chars: Maximum length of a recorded string value before it is truncated.

    Attributes:
        checks: The checks recorded so far, in order.
        redactor: Redactor applied to string values.
        max_chars: Maximum length of a recorded string value.
    """

    def __init__(self, redactor: Redactor = REDACTOR, max_chars: int = DEFAULTS.max_observed_chars):
        self.checks: list[Check] = []
        self.redactor = redactor
        self.max_chars = max_chars

    def check(self, name: str, observed: Any, expected: Any, ok: bool) -> Check:
        """Record a check without raising, whatever its outcome.

        Args:
            name: Name of the check.
            observed: Value observed.
            expected: Value or condition expected.
            ok: Whether the check passed.

        Returns:
            The recorded check.
        """
        recorded = Check(name, self._clip(observed), self._clip(expected), bool(ok))
        self.checks.append(recorded)
        return recorded

    def require(self, name: str, observed: Any, expected: Any, ok: bool | None = None) -> Check:
        """Record a check and raise if it did not pass.

        Args:
            name: Name of the check.
            observed: Value observed.
            expected: Value or condition expected.
            ok: Whether the check passed. When None, the check passes if `observed` equals
                `expected`.

        Returns:
            The recorded check.

        Raises:
            CheckFailed: If the check did not pass.
        """
        recorded = self.check(name, observed, expected, observed == expected if ok is None else ok)
        if not recorded.ok:
            raise CheckFailed(recorded)
        return recorded

    def fail(self, name: str, observed: Any, expected: Any) -> None:
        """Record a failed check and raise.

        Args:
            name: Name of the check.
            observed: Value observed.
            expected: Value or condition expected.

        Raises:
            CheckFailed: Always, carrying the recorded check.
        """
        raise CheckFailed(self.check(name, observed, expected, False))

    def _clip(self, value: Any) -> Any:
        """Return a value suitable for recording in the journal.

        Byte payloads are recorded by size only, and strings are redacted and truncated to
        `max_chars`. The full detail remains available in the raw exchange evidence.

        Args:
            value: Value to record.

        Returns:
            The value, or a redacted and size-limited representation of it.
        """
        if isinstance(value, bytes | bytearray):
            return f"<{len(value)} bytes>"
        if isinstance(value, str):
            value = self.redactor.scrub(value)
            if len(value) > self.max_chars:
                return f"{value[: self.max_chars]}... <{len(value)} chars>"
        return value
