"""Streaming: a well-formed Server-Sent Events stream, ended the way clients expect."""

from __future__ import annotations

import json

from cli_framework import Context

from ..client import sse_events
from . import REGISTRY


@REGISTRY.case("stream.well_formed", needs={"stream"}, tags={"smoke"})
def well_formed(ctx: Context) -> None:
    """A streamed completion: every event is a JSON chunk, exactly one carries a finish
    reason, the content assembles to a non-empty answer, and the stream ends with
    `data: [DONE]`. Clients that wait for [DONE] hang when a layer drops it."""
    t = ctx.target
    exchange = ctx.state.client(t).post_json(
        "/v1/chat/completions",
        {
            "model": t.model,
            "stream": True,
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
        },
        case_id=ctx.case_id,
    )
    ctx.require("http_status", exchange.status, 200)
    content_type = {k.lower(): v for k, v in exchange.headers.items()}.get("content-type", "")
    ctx.require("content_type", content_type.split(";")[0], "text/event-stream")
    events = sse_events(exchange)
    ctx.check("events", len(events), "> 0", bool(events))
    ctx.save("stream.txt", exchange.body.decode("utf-8", "replace"))  # evidence, per case
    chunks, text, finishes = [], "", 0
    for raw in events:
        if raw == "[DONE]":
            continue
        try:
            chunk = json.loads(raw)
        except ValueError:
            ctx.fail("chunk_is_json", raw[:80], "a JSON object")
        chunks.append(chunk)
        for choice in chunk.get("choices", []):
            text += (choice.get("delta") or {}).get("content") or ""
            finishes += choice.get("finish_reason") is not None
    ctx.require("finish_reasons", finishes, 1)
    ctx.require("content_non_empty", len(text.strip()), "> 0", bool(text.strip()))
    ctx.require("ends_with_done", events[-1] if events else None, "[DONE]")
