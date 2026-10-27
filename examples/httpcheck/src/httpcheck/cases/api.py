"""API behaviour every correct JSON service shares: content types, errors, authentication."""

from __future__ import annotations

from va.runner import Context

from . import REGISTRY


@REGISTRY.case("api.json_content_type", needs={"json", "health"})
def json_content_type(ctx: Context) -> None:
    """The health answer is JSON and says so in its Content-Type."""
    t = ctx.target
    exchange = ctx.state.client(t).get(t.health_path, case_id=ctx.case_id)
    content_type = {k.lower(): v for k, v in exchange.headers.items()}.get("content-type", "")
    ctx.require("content_type", content_type.split(";")[0], "application/json")
    try:
        exchange.json()
        ctx.check("body_parses", True, True, True)
    except ValueError as exc:
        ctx.fail("body_parses", str(exc), "a JSON document")


@REGISTRY.case("api.unknown_path", variants=("GET", "POST"))
def unknown_path(ctx: Context) -> None:
    """A path that cannot exist answers 404, for each method: not a 200 page, not a 500."""
    t = ctx.target
    exchange = ctx.state.client(t).request(
        ctx.variant,
        "/va-no-such-path",
        body=b"{}" if ctx.variant == "POST" else None,
        headers={"content-type": "application/json"},
        case_id=ctx.case_id,
    )
    ctx.require("http_status", exchange.status, 404)


@REGISTRY.case("auth.rejects_bad_key", needs={"auth"}, tags={"smoke", "security"})
def rejects_bad_key(ctx: Context) -> None:
    """A wrong credential is refused with 401 on the health path."""
    t = ctx.target
    exchange = ctx.state.client(t).get(
        t.health_path,
        headers={"Authorization": "Bearer va-wrong-key"},
        case_id=ctx.case_id,
    )
    ctx.require("http_status", exchange.status, 401)
