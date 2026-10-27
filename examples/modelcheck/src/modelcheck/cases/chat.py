"""Chat completions: an answer, a valid finish reason, honest token usage, and the latency the
profile allows."""

from __future__ import annotations

from cli_framework import Context

from . import REGISTRY

PROMPTS = {
    "user_only": [{"role": "user", "content": "What is the capital of France?"}],
    "with_system": [
        {"role": "system", "content": "Answer in one sentence."},
        {"role": "user", "content": "What is the capital of France?"},
    ],
}


def complete(ctx: Context, messages: list[dict]) -> dict:
    t = ctx.target
    exchange = ctx.state.client(t).post_json(
        "/v1/chat/completions",
        {"model": t.model, "messages": messages, "max_tokens": 32},
        case_id=ctx.case_id,
    )
    ctx.require("http_status", exchange.status, 200)
    return exchange.json()


@REGISTRY.case("chat.completion", needs={"chat"}, variants=tuple(PROMPTS), tags={"smoke"})
def completion(ctx: Context) -> None:
    """A completion has non-empty content and a finish reason of stop or length; one case,
    run once per prompt shape (the variants)."""
    body = complete(ctx, PROMPTS[ctx.variant])
    choice = (body.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content") or ""
    ctx.require("content_non_empty", len(content), "> 0", len(content) > 0)
    finish = choice.get("finish_reason")
    ctx.require("finish_reason", finish, "stop or length", finish in ("stop", "length"))


@REGISTRY.case("chat.usage_adds_up", needs={"chat"})
def usage_adds_up(ctx: Context) -> None:
    """usage.total_tokens equals prompt_tokens + completion_tokens: billing and quotas read it."""
    usage = complete(ctx, PROMPTS["user_only"]).get("usage") or {}
    parts = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
    ctx.require("total_tokens", usage.get("total_tokens"), parts)


@REGISTRY.case("chat.latency", needs={"chat"}, tags={"perf"}, exclusive=True)
def latency(ctx: Context) -> None:
    """A completion answers within the layer's declared max_latency_ms. Exclusive: with
    --workers, it still runs alone, so other requests in flight do not skew it."""
    t = ctx.target
    exchange = ctx.state.client(t).post_json(
        "/v1/chat/completions",
        {"model": t.model, "messages": PROMPTS["user_only"], "max_tokens": 8},
        case_id=ctx.case_id,
    )
    ctx.require("http_status", exchange.status, 200)
    ctx.require(
        "latency_ms",
        exchange.elapsed_ms,
        f"<= {t.max_latency_ms}",
        exchange.elapsed_ms <= t.max_latency_ms,
    )
