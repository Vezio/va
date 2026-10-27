"""The framework's tunables, in one immutable place.

Timeouts, retry bounds and truncation limits used to be literals scattered across modules.
They live here, named and explained, in a frozen `Defaults`. `DEFAULTS` is the instance the
framework uses; it cannot be mutated, and an embedder that needs different values builds its
own with `dataclasses.replace(DEFAULTS, ...)` and passes it where an API accepts one.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retries, for deliveries only (uploads, notifications), never a test request."""

    attempts: int = 4
    base_delay: float = 1.0
    max_delay: float = 30.0  # also the longest Retry-After honoured; longer stops delivery

    def delay(self, attempt: int) -> float:
        return min(self.base_delay * 2 ** (attempt - 1), self.max_delay)


@dataclass(frozen=True)
class Defaults:
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    http_timeout_s: float = 10.0  # one notification or webhook request
    command_timeout_s: float = 60.0  # an exec: notifier
    kube_timeout_s: float = 20.0  # one read-only cluster call
    max_failures: int = 50  # failures kept in outcome.json; the rest are in results.jsonl
    max_reason_chars: int = 300  # a failure's reason in outcome.json
    max_observed_chars: int = 2000  # a recorded check's observed or expected value
    max_failures_in_message: int = 10  # failures listed in a chat message
    min_secret_length: int = 6  # shorter values are not redacted: they would shred text
    job_budget_s: float = 3000.0  # `job` stops its run after this, unless told otherwise
    job_grace_s: float = 60.0  # between SIGTERM and SIGKILL for the run


DEFAULTS = Defaults()
