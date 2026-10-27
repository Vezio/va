"""A recording HTTP client: every exchange a case makes is kept as evidence.

Raw HTTP on purpose. An SDK normalises away much of what a test exists to see (malformed
bodies, wrong statuses, headers), so cases speak HTTP and record exactly what came back.
Requests are never retried here: a retry hides the failure under test.

Each client is bound to one layer, and its kind's adapter: the adapter's headers go on every
request, and which component answered (`served_by`) is recorded with every exchange.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from va import LayerAdapter
from va.layers import Layered
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
    served_by: str | None = None

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class RecordingClient:
    def __init__(
        self,
        run: RunDir,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 10,
        layer: Layered | None = None,
        adapter: LayerAdapter | None = None,
    ):
        self.run = run
        self.base_url = base_url
        self.token = token
        self.timeout = timeout
        self.layer = layer
        self.adapter = adapter or LayerAdapter()

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
        sent = {
            **(self.adapter.request_headers(self.layer) if self.layer else {}),
            **(headers or {}),
        }
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
        if self.layer is not None and exchange.status is not None:
            exchange.served_by = self.adapter.served_by(self.layer, exchange.headers)
        self.run.append_jsonl(
            "exchanges.jsonl",
            [
                {
                    "case_id": case_id,
                    "method": method,
                    "url": url,
                    "status": exchange.status,
                    "elapsed_ms": exchange.elapsed_ms,
                    "served_by": exchange.served_by,
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
