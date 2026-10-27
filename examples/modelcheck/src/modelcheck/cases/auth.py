"""Authentication cases, which run only on layers that declare the `auth` capability."""

from __future__ import annotations

from va import Context

from modelcheck.registry import REGISTRY


@REGISTRY.case("auth.rejects_bad_key", needs={"auth"}, tags={"smoke", "security"})
def rejects_bad_key(ctx: Context) -> None:
    """Verify that a request with an invalid API key is rejected with the declared status.

    The expected status is `expect.bad_key_status`, which defaults to 401. A layer that does not
    declare the `auth` capability skips this case with the skip class `undeclared`.
    """
    t = ctx.target
    exchange = ctx.state.client(t).get(
        "/v1/models", headers={"Authorization": "Bearer modelcheck-wrong-key"}, case_id=ctx.case_id
    )
    ctx.require("http_status", exchange.status, t.expect.bad_key_status)
