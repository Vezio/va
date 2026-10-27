"""Minimal OpenAI-compatible serving stack with a gateway, a router and an engine.

The tests run the stack in-process, and the local demo runs it as a service:

    python examples/modelcheck/tests/fake_stack.py --gateway-port 8400 --router-port 8401 \
        --engine-port 8402 [--defect gateway_drops_done --defect ...]

As in a real stack, the gateway authenticates requests and forwards them to the router. The
router selects an engine, forwards the request to it and names it in the `x-served-by` header,
which the gateway passes through. Each defect reproduces a realistic fault in one layer, so that
every case can be shown to fail against at least one defect:

    gateway_drops_done        The gateway's streams end without `data: [DONE]`.
    gateway_error_status      The gateway answers an unknown model with 400 instead of 404.
    gateway_any_key           The gateway accepts any key.
    router_drops_usage        The router removes `usage` from completions it forwards.
    engine_empty_content      The engine answers with empty content.
    engine_bad_usage          The engine's usage totals do not add up.
    engine_bare_errors        The engine's errors are plain text instead of a JSON envelope.
    engine_slow               The engine takes one second to answer.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "demo-model"
TOKEN = "demo-gateway-key"
DEFECTS = (
    "gateway_drops_done",
    "gateway_error_status",
    "gateway_any_key",
    "router_drops_usage",
    "engine_empty_content",
    "engine_bad_usage",
    "engine_bare_errors",
    "engine_slow",
)
REPLY = "Paris is the capital of France."
SERVED_BY = "x-served-by"
ENGINE_ID = "engine-0"  # The name the router reports for its only engine.


@dataclass
class FakeStack:
    """The three fake layers, each served on its own local port in a background thread.

    The servers start when the instance is created.

    Attributes:
        defects: The defects to enable, from `DEFECTS`.
        engine_port: The engine's port, or 0 to pick a free one.
        gateway_port: The gateway's port, or 0 to pick a free one.
        router_port: The router's port, or 0 to pick a free one.
        received: The method and path of each request the gateway received.
    """

    defects: set[str] = field(default_factory=set)
    engine_port: int = 0
    gateway_port: int = 0
    router_port: int = 0
    received: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate the defects and start the engine, router and gateway servers.

        Raises:
            ValueError: If a defect is not one of `DEFECTS`.
        """
        unknown = self.defects - set(DEFECTS)
        if unknown:
            raise ValueError(f"unknown defects: {sorted(unknown)}")
        self.engine = ThreadingHTTPServer(("127.0.0.1", self.engine_port), _engine(self))
        self.router = ThreadingHTTPServer(("127.0.0.1", self.router_port), _router(self))
        self.gateway = ThreadingHTTPServer(("127.0.0.1", self.gateway_port), _gateway(self))
        for server in self.servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()

    @property
    def engine_url(self) -> str:
        """The engine's base URL."""
        return f"http://127.0.0.1:{self.engine.server_port}"

    @property
    def router_url(self) -> str:
        """The router's base URL."""
        return f"http://127.0.0.1:{self.router.server_port}"

    @property
    def gateway_url(self) -> str:
        """The gateway's base URL."""
        return f"http://127.0.0.1:{self.gateway.server_port}"

    @property
    def servers(self) -> tuple[ThreadingHTTPServer, ...]:
        """The engine, router and gateway servers."""
        return (self.engine, self.router, self.gateway)

    def close(self) -> None:
        """Stop all servers and release their ports."""
        for server in self.servers:
            server.shutdown()
            server.server_close()


class _Handler(BaseHTTPRequestHandler):
    """Base request handler with helpers shared by all fake layers.

    Attributes:
        stack: The stack this handler belongs to, which holds the enabled defects.
    """

    stack: FakeStack

    def log_message(self, *args):
        """Suppress the default access log on stderr."""
        pass

    def _body(self) -> bytes:
        """Read the request body according to its Content-Length header.

        Returns:
            The body, or an empty bytes object if there is none.
        """
        length = int(self.headers.get("content-length") or 0)
        return self.rfile.read(length) if length else b""

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str = "application/json",
        extra: dict[str, str] | None = None,
    ) -> None:
        """Send a complete response with the given status, body and headers.

        Args:
            status: The HTTP status code.
            body: The response body.
            content_type: The Content-Type header value.
            extra: Additional response headers.
        """
        self.send_response(status)
        self.send_header("content-type", content_type)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str, kind: str) -> None:
        """Send an error response in the OpenAI error envelope.

        With the `engine_bare_errors` defect, the engine sends the message as plain text.

        Args:
            status: The HTTP status code.
            message: The error message.
            kind: The error type reported in the envelope.
        """
        if "engine_bare_errors" in self.stack.defects and isinstance(self, _EngineBase):
            self._send(status, message.encode(), "text/plain")
            return
        body = {"error": {"message": message, "type": kind, "code": status}}
        self._send(status, json.dumps(body).encode())

    def _upstream(
        self, url: str, method: str, body: bytes | None
    ) -> tuple[int, str, bytes, dict[str, str]]:
        """Forward the current request to the layer behind this one.

        Args:
            url: The base URL of the layer behind.
            method: The HTTP method.
            body: The request body to forward.

        Returns:
            The upstream status, content type, body and headers.
        """
        request = urllib.request.Request(
            url + self.path, data=body, method=method, headers={"content-type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return (
                    response.status,
                    response.headers["content-type"],
                    response.read(),
                    dict(response.headers),
                )
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers["content-type"], exc.read(), dict(exc.headers)


class _EngineBase(_Handler):
    """Marker base class that identifies engine handlers for the `engine_bare_errors` defect."""


def _engine(stack: FakeStack) -> type[BaseHTTPRequestHandler]:
    """Create the engine's request handler class for a stack.

    Args:
        stack: The stack whose defects the handler applies.

    Returns:
        The handler class.
    """

    class Engine(_EngineBase):
        """Fake model server that answers health, model list and chat completion requests."""

        def do_GET(self):
            """Answer the health check and the model list."""
            if self.path == "/health":
                return self._send(200, b'{"status": "ok"}')
            if self.path == "/v1/models":
                body = {"object": "list", "data": [{"id": MODEL, "object": "model"}]}
                return self._send(200, json.dumps(body).encode())
            self._error(404, f"no route {self.path}", "NotFoundError")

        def do_POST(self):
            """Answer a chat completion, as a JSON body or as a Server-Sent Events stream."""
            if self.path != "/v1/chat/completions":
                return self._error(404, f"no route {self.path}", "NotFoundError")
            try:
                request = json.loads(self._body())
            except ValueError:
                return self._error(400, "the body is not JSON", "BadRequestError")
            if request.get("model") != MODEL:
                return self._error(
                    404, f"model {request.get('model')!r} not found", "NotFoundError"
                )
            if "engine_slow" in stack.defects:
                time.sleep(1.0)
            content = "" if "engine_empty_content" in stack.defects else REPLY
            prompt_tokens = sum(len(str(m.get("content", "")).split()) for m in request["messages"])
            completion_tokens = len(content.split())
            total = (
                prompt_tokens
                + completion_tokens
                + (5 if "engine_bad_usage" in stack.defects else 0)
            )
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total,
            }
            if not request.get("stream"):
                body = {
                    "id": "chatcmpl-demo",
                    "object": "chat.completion",
                    "model": MODEL,
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": content},
                        }
                    ],
                    "usage": usage,
                }
                return self._send(200, json.dumps(body).encode())
            frames = [{"choices": [{"index": 0, "delta": {"role": "assistant"}}]}]
            frames += [
                {"choices": [{"index": 0, "delta": {"content": word + " "}}]}
                for word in content.split()
            ]
            frames.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            text = (
                "".join(
                    f"data: {json.dumps({'object': 'chat.completion.chunk', **f})}\n\n"
                    for f in frames
                )
                + "data: [DONE]\n\n"
            )
            self._send(200, text.encode(), "text/event-stream")

    Engine.stack = stack
    return Engine


def _router(stack: FakeStack) -> type[BaseHTTPRequestHandler]:
    """Create the router's request handler class for a stack.

    Args:
        stack: The stack whose defects the handler applies.

    Returns:
        The handler class.
    """

    class Router(_Handler):
        """Fake router that forwards to the engine and names it in `x-served-by`."""

        def _forward(self, method: str, body: bytes | None = None) -> None:
            """Forward the request to the engine and relay its response.

            With the `router_drops_usage` defect, `usage` is removed from JSON responses.

            Args:
                method: The HTTP method.
                body: The request body, if any.
            """
            status, kind, data, _ = self._upstream(stack.engine_url, method, body)
            if (
                "router_drops_usage" in stack.defects
                and status == 200
                and kind.startswith("application/json")
            ):
                answer = json.loads(data)
                if isinstance(answer, dict) and answer.pop("usage", None) is not None:
                    data = json.dumps(answer).encode()
            self._send(status, data, kind, {SERVED_BY: ENGINE_ID})

        def do_GET(self):
            """Forward a GET request to the engine."""
            self._forward("GET")

        def do_POST(self):
            """Forward a POST request to the engine."""
            self._forward("POST", self._body())

    Router.stack = stack
    return Router


def _gateway(stack: FakeStack) -> type[BaseHTTPRequestHandler]:
    """Create the gateway's request handler class for a stack.

    Args:
        stack: The stack whose defects the handler applies.

    Returns:
        The handler class.
    """

    class Gateway(_Handler):
        """Fake gateway that authenticates requests and forwards them to the router."""

        def _authorised(self) -> bool:
            """Return whether the request carries the expected bearer token.

            With the `gateway_any_key` defect, every request is accepted.
            """
            if "gateway_any_key" in stack.defects:
                return True
            return self.headers.get("authorization") == f"Bearer {TOKEN}"

        def _forward(self, method: str, body: bytes | None = None) -> None:
            """Authenticate the request, forward it to the router and relay the response.

            Health checks are not authenticated. Only the `x-served-by` header is passed
            through from the router. The gateway defects are applied here.

            Args:
                method: The HTTP method.
                body: The request body, if any.
            """
            stack.received.append({"method": method, "path": self.path})
            if self.path != "/health" and not self._authorised():
                return self._error(401, "invalid API key", "AuthenticationError")
            status, kind, data, headers = self._upstream(stack.router_url, method, body)
            if (
                status == 404
                and "model" in data.decode(errors="replace")
                and ("gateway_error_status" in stack.defects)
            ):
                return self._error(400, "invalid model", "BadRequestError")
            if "gateway_drops_done" in stack.defects and kind.startswith("text/event-stream"):
                data = data.replace(b"data: [DONE]\n\n", b"")
            passed = {k: v for k, v in headers.items() if k.lower() == SERVED_BY}
            self._send(status, data, kind, passed)

        def do_GET(self):
            """Forward a GET request to the router."""
            self._forward("GET")

        def do_POST(self):
            """Forward a POST request to the router."""
            self._forward("POST", self._body())

    Gateway.stack = stack
    return Gateway


def main() -> None:
    """Start the fake stack from the command line and serve until interrupted."""
    parser = argparse.ArgumentParser(description="a fake OpenAI-compatible stack for the demo")
    parser.add_argument("--gateway-port", type=int, default=8400)
    parser.add_argument("--router-port", type=int, default=8401)
    parser.add_argument("--engine-port", type=int, default=8402)
    parser.add_argument("--defect", action="append", default=[], choices=DEFECTS)
    args = parser.parse_args()
    stack = FakeStack(set(args.defect), args.engine_port, args.gateway_port, args.router_port)
    defects = ", ".join(sorted(stack.defects)) or "none"
    print(  # noqa: T201
        f"gateway {stack.gateway_url}, router {stack.router_url}, engine {stack.engine_url}, "
        f"defects: {defects}",
        flush=True,
    )
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        stack.close()


if __name__ == "__main__":
    main()
