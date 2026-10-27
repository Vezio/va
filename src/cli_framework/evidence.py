"""The check journal: record what you observed, then decide.

Every assertion records its observation **before** it decides, on passes as well as failures.
A failure then shows the value that caused it, and a pass shows what it checked. `require` and
`fail` record a failed check and raise in one step, so the deciding check is never missing.

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
    """A recorded check failed; the runner turns this into a `fail` verdict."""

    def __init__(self, check: Check):
        super().__init__(f"{check.name}: expected {check.expected!r}, got {check.observed!r}")
        self.check = check


class Journal:
    def __init__(self, redactor: Redactor = REDACTOR, max_chars: int = DEFAULTS.max_observed_chars):
        self.checks: list[Check] = []
        self.redactor = redactor
        self.max_chars = max_chars

    def check(self, name: str, observed: Any, expected: Any, ok: bool) -> Check:
        recorded = Check(name, self._clip(observed), self._clip(expected), bool(ok))
        self.checks.append(recorded)
        return recorded

    def require(self, name: str, observed: Any, expected: Any, ok: bool | None = None) -> Check:
        """Record, then raise CheckFailed unless ok (default: observed == expected)."""
        recorded = self.check(name, observed, expected, observed == expected if ok is None else ok)
        if not recorded.ok:
            raise CheckFailed(recorded)
        return recorded

    def fail(self, name: str, observed: Any, expected: Any) -> None:
        raise CheckFailed(self.check(name, observed, expected, False))

    def _clip(self, value: Any) -> Any:
        """Large payloads are recorded by size; the raw exchange keeps the detail."""
        if isinstance(value, bytes | bytearray):
            return f"<{len(value)} bytes>"
        if isinstance(value, str):
            value = self.redactor.scrub(value)
            if len(value) > self.max_chars:
                return f"{value[: self.max_chars]}... <{len(value)} chars>"
        return value
