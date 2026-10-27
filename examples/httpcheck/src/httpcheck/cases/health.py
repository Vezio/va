"""Cases that check the availability and latency of a target's health endpoint."""

from __future__ import annotations

from va import Context

from httpcheck.registry import REGISTRY


@REGISTRY.case("health.status", needs={"health"}, tags={"smoke"})
def health_status(ctx: Context) -> None:
    """Verify that a GET on the declared health path returns 200.

    A connection failure is raised as an error rather than recorded as a failed check,
    because preflight is expected to detect an unreachable target before cases run.

    Args:
        ctx: The case context, providing the target, shared state and check recording.

    Raises:
        ConnectionError: If the request could not be completed.
    """
    t = ctx.target
    exchange = ctx.state.client(t).get(t.health_path, case_id=ctx.case_id)
    if exchange.error:
        raise ConnectionError(exchange.error)
    ctx.require("http_status", exchange.status, 200)


@REGISTRY.case("health.latency", needs={"health"}, tags={"perf"})
def health_latency(ctx: Context) -> None:
    """Verify that the health path responds within the target's `max_latency_ms`.

    Args:
        ctx: The case context, providing the target, shared state and check recording.
    """
    t = ctx.target
    exchange = ctx.state.client(t).get(t.health_path, case_id=ctx.case_id)
    ctx.require("http_status", exchange.status, 200)
    ctx.require(
        "latency_ms",
        exchange.elapsed_ms,
        f"<= {t.max_latency_ms}",
        exchange.elapsed_ms <= t.max_latency_ms,
    )
