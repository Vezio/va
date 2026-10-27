"""Health: the service answers its health path, quickly."""

from __future__ import annotations

from cli_framework.runner import Context

from . import REGISTRY


@REGISTRY.case("health.status", needs={"health"}, tags={"smoke"})
def health_status(ctx: Context) -> None:
    """GETs the declared health path and requires a 200. A refused connection is an error
    (no verdict), not a fail: preflight should have caught it."""
    t = ctx.target
    exchange = ctx.state.client(t).get(t.health_path, case_id=ctx.case_id)
    if exchange.error:
        raise ConnectionError(exchange.error)
    ctx.require("http_status", exchange.status, 200)


@REGISTRY.case("health.latency", needs={"health"}, tags={"perf"})
def health_latency(ctx: Context) -> None:
    """The health path answers within the target's declared max_latency_ms."""
    t = ctx.target
    exchange = ctx.state.client(t).get(t.health_path, case_id=ctx.case_id)
    ctx.require("http_status", exchange.status, 200)
    ctx.require(
        "latency_ms",
        exchange.elapsed_ms,
        f"<= {t.max_latency_ms}",
        exchange.elapsed_ms <= t.max_latency_ms,
    )
