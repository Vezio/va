"""A real HTTP server on a local socket, not a mock, with flags for the defects the cases
exist to catch. Every case must be proven to fail against its defect here: a case that has
never failed has not been tested."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class Defects:
    health_status: int = 200
    health_delay_s: float = 0.0
    health_content_type: str = "application/json"
    health_body: bytes = b'{"ok": true}'
    unknown_path_status: int = 404
    accept_any_key: bool = False


@dataclass
class FakeService:
    token: str = "fake-service-token-123"
    defects: Defects = field(default_factory=Defects)
    received: list[dict] = field(default_factory=list)

    def __post_init__(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence the stdlib's stderr access log
                pass

            def _answer(self):
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else b""
                service.received.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": body,
                    }
                )
                d = service.defects
                if self.path == "/health":
                    auth = self.headers.get("authorization", "")
                    if not d.accept_any_key and auth and auth != f"Bearer {service.token}":
                        return self._send(401, b'{"error": "bad key"}', "application/json")
                    time.sleep(d.health_delay_s)
                    return self._send(d.health_status, d.health_body, d.health_content_type)
                if self.path == "/webhook":
                    return self._send(200, b"ok", "text/plain")
                return self._send(
                    d.unknown_path_status, b'{"error": "not found"}', "application/json"
                )

            def _send(self, status, body, content_type):
                self.send_response(status)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _answer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def webhook_payloads(self) -> list[dict]:
        return [json.loads(r["body"]) for r in self.received if r["path"] == "/webhook"]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
