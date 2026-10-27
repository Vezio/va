"""Local HTTP server that stands in for a deployed service in the httpcheck tests.

The server listens on a real socket rather than mocking the client, and exposes flags for the
defects the cases are designed to detect. Each case has a test that enables its defect and
confirms that the case fails, which shows that the case can detect the problem.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class Defects:
    """Configurable defects of the fake service. The defaults describe a correct service.

    Attributes:
        health_status: Status returned by the health endpoint.
        health_delay_s: Delay before the health endpoint responds, in seconds.
        health_content_type: Content-Type of the health response.
        health_body: Body of the health response.
        unknown_path_status: Status returned for any unrecognised path.
        accept_any_key: Whether the health endpoint accepts any bearer token.
    """

    health_status: int = 200
    health_delay_s: float = 0.0
    health_content_type: str = "application/json"
    health_body: bytes = b'{"ok": true}'
    unknown_path_status: int = 404
    accept_any_key: bool = False


@dataclass
class FakeService:
    """Fake HTTP service running on a background thread on 127.0.0.1.

    The server starts when the instance is created. Call `close` to stop it.

    Attributes:
        token: Bearer token that the health endpoint accepts.
        defects: Defects to apply to responses. They can be changed while the server runs.
        received: Each request received, as a dict of method, path, headers and body.
        server: The underlying HTTP server.
        thread: Thread that runs the server.
    """

    token: str = "fake-service-token-123"
    defects: Defects = field(default_factory=Defects)
    received: list[dict] = field(default_factory=list)

    def __post_init__(self):
        """Start the HTTP server on a free port in a daemon thread."""
        service = self

        class Handler(BaseHTTPRequestHandler):
            """Request handler that records each request and applies the configured defects."""

            def log_message(self, *args):
                """Suppress the default access log, which the standard library writes to stderr.

                Args:
                    *args: Format string and values, ignored.
                """
                pass

            def _answer(self):
                """Record the request and send the response for its path.

                `/health` honours the configured defects and rejects a wrong bearer token
                with 401. `/webhook` accepts notification payloads. Any other path returns
                `unknown_path_status`.
                """
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
                """Write a complete response.

                Args:
                    status: HTTP status code.
                    body: Response body.
                    content_type: Value of the Content-Type header.
                """
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
        """The base URL of the running server."""
        return f"http://127.0.0.1:{self.server.server_port}"

    def webhook_payloads(self) -> list[dict]:
        """Return the JSON payloads posted to `/webhook`, in the order received.

        Returns:
            The decoded payloads.
        """
        return [json.loads(r["body"]) for r in self.received if r["path"] == "/webhook"]

    def close(self) -> None:
        """Stop the server and close its socket."""
        self.server.shutdown()
        self.server.server_close()
