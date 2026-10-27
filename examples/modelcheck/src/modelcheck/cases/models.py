"""The model list: the layer serves the model the profile declares."""

from __future__ import annotations

from va import Context

from . import REGISTRY


@REGISTRY.case("models.lists_declared_model", needs={"models"}, tags={"smoke"})
def lists_declared_model(ctx: Context) -> None:
    """GET /v1/models answers 200 and lists the declared model."""
    t = ctx.target
    exchange = ctx.state.client(t).get("/v1/models", case_id=ctx.case_id)
    ctx.require("http_status", exchange.status, 200)
    ids = [m.get("id") for m in exchange.json().get("data", [])]
    ctx.require("model_listed", t.model in ids, True)
    ctx.check("models", ids, f"contains {t.model}", t.model in ids)
