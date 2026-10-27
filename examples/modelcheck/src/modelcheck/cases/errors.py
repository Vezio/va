"""Error handling cases.

These cases check that each layer returns the status its profile declares, inside a JSON error
envelope that clients can parse.
"""

from __future__ import annotations

from va import Context

from . import REGISTRY


@REGISTRY.case("errors.unknown_model", needs={"chat"})
def unknown_model(ctx: Context) -> None:
    """Verify that a request for an unknown model is rejected with the declared status.

    The expected status is the layer's `expect.unknown_model_status`, and the body must be an
    OpenAI error envelope of the form {"error": {"message", "type"}}. Each layer declares its own
    expected status, so when layers behave differently the results show which layer differs.
    """
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
