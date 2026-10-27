"""Bounded retries for result deliveries.

Uploads and notifications are retried within bounds, so that a transient delivery error does not
lose evidence. Requests under test are not retried, because a retry would hide the failure being
tested. This module must therefore not be used in any code path that a case runs.
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
    """Exception an attempt raises to request a specific wait, such as an HTTP Retry-After.

    Args:
        seconds: Number of seconds to wait before the next attempt.
        why: Optional message. Defaults to a description of the requested wait.

    Attributes:
        seconds: Number of seconds to wait before the next attempt.
    """

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
    """Call `attempt` up to `policy.attempts` times and return its first successful result.

    A `RetryAfter` sets the delay before the next attempt. If the requested delay exceeds
    `policy.max_delay`, the exception is re-raised immediately.

    Args:
        attempt: Callable that performs one attempt.
        what: Description of the operation, used in log messages.
        policy: Number of attempts and the delay between them.
        retry_on: Exception types that cause another attempt.
        sleep: Function used to wait between attempts.

    Returns:
        The result of the first successful attempt.

    Raises:
        RetryAfter: If an attempt requests a wait longer than `policy.max_delay`.
        BaseException: The last error raised, when every attempt fails.
    """
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
