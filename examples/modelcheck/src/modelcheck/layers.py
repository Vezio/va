"""The kinds of layer an OpenAI-compatible serving stack has, from the client inwards.

    proxy   an API gateway or proxy: authenticates, may translate, forwards to a router or
            an engine; passes through the header that names the backend that answered
    router  routes each request to one of several engines, and names the one it picked in
            `x-served-by`
    engine  the model server itself: what answers is the layer

A new kind is one `register` call here, and an adapter only if calling it differs.
"""

from __future__ import annotations

from va import AnswersItself, HeaderServedBy, LayerKinds

SERVED_BY = "x-served-by"

KINDS = LayerKinds()
KINDS.register(
    "proxy",
    depth=0,
    description="an API gateway or proxy in front of a router or an engine",
    adapter=HeaderServedBy(SERVED_BY),
)
KINDS.register(
    "router",
    depth=1,
    description="routes each request to one of several engines",
    adapter=HeaderServedBy(SERVED_BY),
)
KINDS.register("engine", depth=2, description="the model server", adapter=AnswersItself())
