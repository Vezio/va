"""Framework tunables collected in a single immutable object.

Timeouts, retry bounds and truncation limits are defined here as named fields of the frozen
`Defaults` dataclass instead of as literals in individual modules. `DEFAULTS` is the instance the
framework uses and cannot be mutated. An embedder that needs different values creates its own
instance with `dataclasses.replace(DEFAULTS, ...)` and passes it to the APIs that accept one.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry policy for deliveries such as uploads and notifications.

    The policy is not applied to test requests, so that a transient failure in the system under
    test is reported rather than hidden.

    Attributes:
        attempts: Maximum number of attempts, including the first.
        base_delay: Delay in seconds before the first retry. Later delays double each time.
        max_delay: Upper bound in seconds for any delay. It is also the longest `Retry-After`
            value honoured; a longer value stops the delivery.
    """

    attempts: int = 4
    base_delay: float = 1.0
    max_delay: float = 30.0

    def delay(self, attempt: int) -> float:
        """Return the delay before the given retry attempt.

        Args:
            attempt: One-based number of the attempt that failed.

        Returns:
            The exponential backoff delay in seconds, capped at `max_delay`.
        """
        return min(self.base_delay * 2 ** (attempt - 1), self.max_delay)


@dataclass(frozen=True)
class Defaults:
    """Timeouts, retry bounds and truncation limits used throughout the framework.

    Attributes:
        retry: Retry policy for uploads and notifications.
        http_timeout_s: Timeout in seconds for one notification or webhook request.
        command_timeout_s: Timeout in seconds for an `exec:` notifier command.
        kube_timeout_s: Timeout in seconds for one read-only cluster call.
        max_failures: Number of failures kept in outcome.json. All failures remain in
            results.jsonl.
        max_reason_chars: Maximum length of a failure reason in outcome.json.
        max_observed_chars: Maximum length of a recorded check's observed or expected value.
        max_failures_in_message: Number of failures listed in a chat message.
        min_secret_length: Minimum length of a value that is redacted. Shorter values are not
            redacted, because replacing them would corrupt unrelated text.
        job_budget_s: Time in seconds after which the `job` command stops its run, unless a
            different budget is configured.
        job_grace_s: Time in seconds between SIGTERM and SIGKILL when the run is stopped.
    """

    retry: RetryPolicy = field(default_factory=RetryPolicy)
    http_timeout_s: float = 10.0
    command_timeout_s: float = 60.0
    kube_timeout_s: float = 20.0
    max_failures: int = 50
    max_reason_chars: int = 300
    max_observed_chars: int = 2000
    max_failures_in_message: int = 10
    min_secret_length: int = 6
    job_budget_s: float = 3000.0
    job_grace_s: float = 60.0


DEFAULTS = Defaults()
