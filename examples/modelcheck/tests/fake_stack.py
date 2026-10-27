"""A tiny OpenAI-compatible serving stack: an engine, and a gateway in front of it.

The tests run it in-process; the local demo runs it as a service:

    python examples/modelcheck/tests/fake_stack.py --engine-port 8401 --gateway-port 8400 \
        [--defect gateway_drops_done --defect ...]

Like a real stack, the gateway authenticates, then forwards to the engine. Each defect is a
real-world way a layer goes wrong, so every case can be proven to fail against one:

    gateway_drops_done        the gateway's streams end without `data: [DONE]`
    gateway_error_status      the gateway answers an unknown model with 400, not 404
    gateway_any_key           the gateway accepts any key
    engine_empty_content      the engine answers with empty content
    engine_bad_usage          the engine's usage totals do not add up
    engine_bare_errors        the engine's errors are plain text, not a JSON envelope
    engine_slow               the engine takes a second to answer
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
    "engine_empty_content",
    "engine_bad_usage",
    "engine_bare_errors",
    "engine_slow",
)
REPLY = "Paris is the capital of France."


@dataclass
class FakeStack:
    defects: set[str] = field(default_factory=set)
    engine_port: int = 0
    gateway_port: int = 0
    received: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        unknown = self.defects - set(DEFECTS)
        if unknown:
            raise ValueError(f"unknown defects: {sorted(unknown)}")
        self.engine = ThreadingHTTPServer(("127.0.0.1", self.engine_port), _engine(self))
        self.gateway = ThreadingHTTPServer(("127.0.0.1", self.gateway_port), _gateway(self))
        for server in (self.engine, self.gateway):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    @property
    def engine_url(self) -> str:
        return f"http://127.0.0.1:{self.engine.server_port}"

    @property
    def gateway_url(self) -> str:
        return f"http://127.0.0.1:{self.gateway.server_port}"

    def close(self) -> None:
        for server in (self.engine, self.gateway):
            server.shutdown()
            server.server_close()


class _Handler(BaseHTTPRequestHandler):
    stack: FakeStack

    def log_message(self, *args):  # no stderr access log
        pass

    def _body(self) -> bytes:
        length = int(self.headers.get("content-length") or 0)
        return self.rfile.read(length) if length else b""

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str, kind: str) -> None:
        if "engine_bare_errors" in self.stack.defects and isinstance(self, _EngineBase):
            self._send(status, message.encode(), "text/plain")
            return
        body = {"error": {"message": message, "type": kind, "code": status}}
        self._send(status, json.dumps(body).encode())


class _EngineBase(_Handler):
    pass


def _engine(stack: FakeStack) -> type[BaseHTTPRequestHandler]:
    class Engine(_EngineBase):
        def do_GET(self):
            if self.path == "/health":
                return self._send(200, b'{"status": "ok"}')
            if self.path == "/v1/models":
                body = {"object": "list", "data": [{"id": MODEL, "object": "model"}]}
                return self._send(200, json.dumps(body).encode())
            self._error(404, f"no route {self.path}", "NotFoundError")

        def do_POST(self):
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


def _gateway(stack: FakeStack) -> type[BaseHTTPRequestHandler]:
    class Gateway(_Handler):
        def _authorised(self) -> bool:
            if "gateway_any_key" in stack.defects:
                return True
            return self.headers.get("authorization") == f"Bearer {TOKEN}"

        def _forward(self, method: str, body: bytes | None = None) -> None:
            stack.received.append({"method": method, "path": self.path})
            if self.path != "/health" and not self._authorised():
                return self._error(401, "invalid API key", "AuthenticationError")
            request = urllib.request.Request(
                stack.engine_url + self.path,
                data=body,
                method=method,
                headers={"content-type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    status, kind, data = (
                        response.status,
                        response.headers["content-type"],
                        response.read(),
                    )
            except urllib.error.HTTPError as exc:
                status, kind, data = exc.code, exc.headers["content-type"], exc.read()
            if (
                status == 404
                and "model" in data.decode(errors="replace")
                and ("gateway_error_status" in stack.defects)
            ):
                return self._error(400, "invalid model", "BadRequestError")
            if "gateway_drops_done" in stack.defects and kind.startswith("text/event-stream"):
                data = data.replace(b"data: [DONE]\n\n", b"")
            self._send(status, data, kind)

        def do_GET(self):
            self._forward("GET")

        def do_POST(self):
            self._forward("POST", self._body())

    Gateway.stack = stack
    return Gateway


def main() -> None:
    parser = argparse.ArgumentParser(description="a fake OpenAI-compatible stack for the demo")
    parser.add_argument("--engine-port", type=int, default=8401)
    parser.add_argument("--gateway-port", type=int, default=8400)
    parser.add_argument("--defect", action="append", default=[], choices=DEFECTS)
    args = parser.parse_args()
    stack = FakeStack(set(args.defect), args.engine_port, args.gateway_port)
    defects = ", ".join(sorted(stack.defects)) or "none"
    print(f"engine {stack.engine_url}, gateway {stack.gateway_url}, defects: {defects}", flush=True)  # noqa: T201
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        stack.close()


if __name__ == "__main__":
    main()
