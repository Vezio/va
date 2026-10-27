"""Redaction of secrets from logs, run files and notifications.

Redaction is applied in three places: at the logging handlers, because HTTP libraries log
request headers at DEBUG level; in every run-file writer, because a server can echo a
credential inside a response body; and in every notification.

Secrets belong to the process, which has one environment and one set of logs, so a single
process-wide `Redactor`, `REDACTOR`, is shared by the logging filters and the writers. It is
the only module-level mutable state in va. Tests and embedding applications can create their
own `Redactor` and pass it to APIs that accept one.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Any

from .defaults import DEFAULTS


class Redactor:
    """A set of registered secret values and patterns that are replaced in text.

    Each pattern has one capturing group. The group is kept and the rest of the match is
    replaced with the sentinel.

    Args:
        patterns: The patterns to apply. Defaults to `PATTERNS`.
        min_length: The minimum length of a value that `add` registers.

    Attributes:
        SENTINEL: The text that replaces a secret.
        PATTERNS: The default patterns, covering bearer tokens, authorization headers and API
            key headers.
        min_length: The minimum length of a value that `add` registers.
    """

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
        """Register secret values.

        Empty values and values shorter than `min_length` are ignored, because replacing them
        would corrupt ordinary text.

        Args:
            *values: The values to register. None is accepted and ignored.
        """
        self._secrets.update(v for v in values if v and len(v) >= self.min_length)

    def add_pattern(self, pattern: str | re.Pattern[str]) -> None:
        """Add a pattern to apply when scrubbing.

        Args:
            pattern: A regular expression with one capturing group. The group is kept and the
                rest of the match is replaced.
        """
        self._patterns.append(re.compile(pattern) if isinstance(pattern, str) else pattern)

    def clear(self) -> None:
        """Forget every registered secret value. Patterns are kept."""
        self._secrets.clear()

    def scrub(self, text: str) -> str:
        """Replace registered secrets and pattern matches in text.

        Secrets are replaced longest first, so that a secret containing another is replaced
        whole.

        Args:
            text: The text to scrub.

        Returns:
            The redacted text.
        """
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(secret, self.SENTINEL)
        for pattern in self._patterns:
            text = pattern.sub(rf"\1{self.SENTINEL}", text)
        return text

    def value(self, value: Any) -> Any:
        """Scrub every string inside a JSON-like value.

        Dictionaries are copied with their values scrubbed, lists and tuples are returned as
        lists, and other values are returned unchanged.

        Args:
            value: The value to scrub.

        Returns:
            The redacted value.
        """
        if isinstance(value, str):
            return self.scrub(value)
        if isinstance(value, dict):
            return {k: self.value(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.value(v) for v in value]
        return value

    def contains_secret(self, text: str) -> bool:
        """Return whether text contains any registered secret value.

        Args:
            text: The text to check.

        Returns:
            True if a registered secret occurs in the text.
        """
        return any(secret in text for secret in self._secrets)

    def secret_from_env(
        self, name: str, *, required: bool = True, environ: dict[str, str] | None = None
    ) -> str | None:
        """Read a secret from the environment and register it for redaction.

        Credentials should be read through this method, so that each one is registered before
        it can appear in a log.

        Args:
            name: The environment variable name.
            required: Whether a missing or empty variable is an error.
            environ: An optional mapping to read from instead of `os.environ`.

        Returns:
            The stripped value, or None if it is missing and not required.

        Raises:
            KeyError: If the variable is missing or empty and `required` is true.
        """
        value = (environ if environ is not None else os.environ).get(name, "").strip()
        if not value:
            if required:
                raise KeyError(f"{name} is not set")
            return None
        self.add(value)
        return value


REDACTOR = Redactor()
