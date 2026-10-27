"""Authentication: only on layers that declare it."""

from __future__ import annotations

from cli_framework import Context

from . import REGISTRY


@REGISTRY.case("auth.rejects_bad_key", needs={"auth"}, tags={"smoke", "security"})
def rejects_bad_key(ctx: Context) -> None:
    """A wrong key is refused with the declared status (expect.bad_key_status, usually 401).
    A layer that does not declare `auth` skips this with the class `undeclared`."""
    t = ctx.target
    exchange = ctx.state.client(t).get(
        "/v1/models", headers={"Authorization": "Bearer modelcheck-wrong-key"}, case_id=ctx.case_id
    )
    ctx.require("http_status", exchange.status, t.expect.bad_key_status)
