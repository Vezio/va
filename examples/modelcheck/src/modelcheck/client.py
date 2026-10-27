"""A recording HTTP client: every exchange a case makes is kept as evidence.

Raw HTTP on purpose. An SDK normalises away much of what a test exists to see (malformed
bodies, wrong statuses, headers), so cases speak HTTP and record exactly what came back.
Requests are never retried here: a retry hides the failure under test.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from va.rundir import RunDir


@dataclass
class Exchange:
    method: str
    url: str
    status: int | None
    headers: dict[str, str]
    body: bytes
    elapsed_ms: float
    error: str | None = None

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class RecordingClient:
    def __init__(
        self, run: RunDir, base_url: str, *, token: str | None = None, timeout: float = 10
    ):
        self.run = run
        self.base_url = base_url
        self.token = token
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        case_id: str = "",
    ) -> Exchange:
        url = self.base_url + path
        sent = dict(headers or {})
        if self.token and "authorization" not in {k.lower() for k in sent}:
            sent["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=body, headers=sent, method=method)
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                exchange = Exchange(
                    method, url, response.status, dict(response.headers), response.read(), 0
                )
        except urllib.error.HTTPError as exc:
            exchange = Exchange(method, url, exc.code, dict(exc.headers or {}), exc.read(), 0)
        except (urllib.error.URLError, OSError) as exc:
            exchange = Exchange(method, url, None, {}, b"", 0, str(getattr(exc, "reason", exc)))
        exchange.elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        self.run.append_jsonl(
            "exchanges.jsonl",
            [
                {
                    "case_id": case_id,
                    "method": method,
                    "url": url,
                    "status": exchange.status,
                    "elapsed_ms": exchange.elapsed_ms,
                    "request_headers": sent,  # append_jsonl redacts every value
                    "response_headers": exchange.headers,
                    "response_body": exchange.body[:4096].decode("utf-8", "replace"),
                    "error": exchange.error,
                }
            ],
        )
        return exchange

    def get(self, path: str, **kwargs) -> Exchange:
        return self.request("GET", path, **kwargs)

    def post_json(self, path: str, body: Any, **kwargs) -> Exchange:
        headers = {"content-type": "application/json", **kwargs.pop("headers", {})}
        return self.request("POST", path, body=json.dumps(body).encode(), headers=headers, **kwargs)


def sse_events(exchange: Exchange) -> list[str]:
    """The `data:` payloads of a Server-Sent Events body, in order (`[DONE]` included)."""
    text = exchange.body.decode("utf-8", "replace")
    return [line[len("data:") :].strip() for line in text.splitlines() if line.startswith("data:")]
