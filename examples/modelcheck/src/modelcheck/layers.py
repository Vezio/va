"""Layer kinds of an OpenAI-compatible serving stack, listed from the client inwards.

The registered kinds are:

    proxy   An API gateway or proxy. It authenticates requests, may translate them, and
            forwards them to a router or an engine. It passes through the header that names
            the backend that answered.
    router  Routes each request to one of several engines and names the selected engine in
            the `x-served-by` response header.
    engine  The model server itself. The engine is the component that answers.

Adding a kind requires one `register` call in this module, plus an adapter only if requests to
that kind are made differently.
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
