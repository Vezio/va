"""Bounded retries, for deliveries only.

Uploads and notifications are retried within bounds: a retried upload preserves evidence.
Requests under test are never retried: a retry hides the failure under test. Keep this module
out of every code path a case uses.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from .defaults import DEFAULTS, RetryPolicy
from .log import get_logger

log = get_logger("retry")
T = TypeVar("T")


class RetryAfter(Exception):
    """Raise from an attempt to ask for a specific wait (an HTTP Retry-After)."""

    def __init__(self, seconds: float, why: str = ""):
        super().__init__(why or f"retry after {seconds}s")
        self.seconds = seconds


def call(
    attempt: Callable[[], T],
    *,
    what: str,
    policy: RetryPolicy = DEFAULTS.retry,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call `attempt` up to `policy.attempts` times; re-raise the last error."""
    last: BaseException | None = None
    for number in range(1, policy.attempts + 1):
        try:
            return attempt()
        except RetryAfter as exc:
            if exc.seconds > policy.max_delay:
                raise
            delay, last = exc.seconds, exc
        except retry_on as exc:
            delay, last = policy.delay(number), exc
        if number < policy.attempts:
            log.warning(
                "%s failed (%s); attempt %d of %d in %.0fs",
                what,
                last,
                number + 1,
                policy.attempts,
                delay,
            )
            sleep(delay)
    assert last is not None
    raise last
