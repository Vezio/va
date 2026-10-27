"""Keep secrets out of everything the tool writes.

Redaction runs in three places on purpose: at the logging handlers (HTTP libraries log
request headers at DEBUG), in every run-file writer (a server can echo a credential inside a
body) and in every notification.

Secrets are process-wide (one environment, one set of logs), so there is one process-wide
`Redactor`, `REDACTOR`, which the logging filters and writers share. It is the only
module-level mutable state in cli_framework, and it is deliberate. Tests and embedders can build
their own `Redactor` and pass it where an API accepts one.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Any

from .defaults import DEFAULTS


class Redactor:
    """Registered secret values plus patterns whose first group is kept and the rest replaced."""

    SENTINEL = "***REDACTED***"
    PATTERNS = (
        re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
        re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]{8,}"),
        re.compile(r"(?i)(x-api-key[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]{8,}"),
    )

    def __init__(
        self,
        patterns: Iterable[re.Pattern[str]] | None = None,
        min_length: int = DEFAULTS.min_secret_length,
    ):
        self._secrets: set[str] = set()
        self._patterns: list[re.Pattern[str]] = list(
            self.PATTERNS if patterns is None else patterns
        )
        self.min_length = min_length

    def add(self, *values: str | None) -> None:
        """Register values. Very short ones are ignored: they would shred ordinary text."""
        self._secrets.update(v for v in values if v and len(v) >= self.min_length)

    def add_pattern(self, pattern: str | re.Pattern[str]) -> None:
        """A pattern with one group: the group is kept, the rest of the match is replaced."""
        self._patterns.append(re.compile(pattern) if isinstance(pattern, str) else pattern)

    def clear(self) -> None:
        self._secrets.clear()

    def scrub(self, text: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):  # longest first
            text = text.replace(secret, self.SENTINEL)
        for pattern in self._patterns:
            text = pattern.sub(rf"\1{self.SENTINEL}", text)
        return text

    def value(self, value: Any) -> Any:
        """Scrub every string inside a JSON-like value."""
        if isinstance(value, str):
            return self.scrub(value)
        if isinstance(value, dict):
            return {k: self.value(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.value(v) for v in value]
        return value

    def contains_secret(self, text: str) -> bool:
        return any(secret in text for secret in self._secrets)

    def secret_from_env(
        self, name: str, *, required: bool = True, environ: dict[str, str] | None = None
    ) -> str | None:
        """Read a secret and register it in the same step: the only sanctioned way to read a
        credential, because a secret read any other way is one a debug log can leak."""
        value = (environ if environ is not None else os.environ).get(name, "").strip()
        if not value:
            if required:
                raise KeyError(f"{name} is not set")
            return None
        self.add(value)
        return value


REDACTOR = Redactor()
