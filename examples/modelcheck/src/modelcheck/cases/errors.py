"""Errors: the status the profile declares for each layer, in a JSON error envelope a client
can read."""

from __future__ import annotations

from cli_framework import Context

from . import REGISTRY


@REGISTRY.case("errors.unknown_model", needs={"chat"})
def unknown_model(ctx: Context) -> None:
    """A model that does not exist is refused with the status this layer declares
    (expect.unknown_model_status), in an OpenAI error envelope: {"error": {"message", "type"}}.
    One case, one declaration per layer: where layers disagree, the columns show which one."""
    t = ctx.target
    exchange = ctx.state.client(t).post_json(
        "/v1/chat/completions",
        {"model": "no-such-model", "messages": [{"role": "user", "content": "hi"}]},
        case_id=ctx.case_id,
    )
    ctx.require("http_status", exchange.status, t.expect.unknown_model_status)
    try:
        error = exchange.json().get("error")
    except ValueError:
        ctx.fail("error_envelope", exchange.body[:80].decode(errors="replace"), "a JSON body")
    ctx.require("error_envelope", isinstance(error, dict), True)
    ctx.require("error_message", bool(error.get("message")), True)
    ctx.require("error_type", isinstance(error.get("type"), str), True)
