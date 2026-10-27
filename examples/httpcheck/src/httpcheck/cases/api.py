"""Cases for behaviour common to JSON services: content types, errors and authentication."""

from __future__ import annotations

from va import Context

from httpcheck.registry import REGISTRY


@REGISTRY.case("api.json_content_type", needs={"json", "health"})
def json_content_type(ctx: Context) -> None:
    """Verify that the health response is JSON and declares it in its Content-Type header.

    Args:
        ctx: The case context, providing the target, shared state and check recording.
    """
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
    """Verify that a request to a nonexistent path returns 404.

    The case runs once per HTTP method variant. A 200 page or a 500 error both fail it.

    Args:
        ctx: The case context; `ctx.variant` is the HTTP method to use.
    """
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
    """Verify that the health path rejects an invalid bearer token with 401.

    Args:
        ctx: The case context, providing the target, shared state and check recording.
    """
    t = ctx.target
    exchange = ctx.state.client(t).get(
        t.health_path,
        headers={"Authorization": "Bearer va-wrong-key"},
        case_id=ctx.case_id,
    )
    ctx.require("http_status", exchange.status, 401)
