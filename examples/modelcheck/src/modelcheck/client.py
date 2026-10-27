"""HTTP client that records every exchange a case makes as evidence in the run directory.

The client uses plain HTTP rather than an OpenAI SDK, because an SDK normalises details that the
cases need to observe, such as malformed bodies, unexpected statuses and headers. Requests are
not retried, so that a transient failure is reported rather than hidden.

Each client is bound to one layer and to the adapter for that layer's kind. The adapter's
headers are added to every request, and the component that answered (`served_by`) is recorded
with every exchange.
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
    """One HTTP request and the response or error it produced.

    Attributes:
        method: The HTTP method of the request.
        url: The full request URL.
        status: The response status code, or None if no response was received.
        headers: The response headers.
        body: The raw response body.
        elapsed_ms: The time from sending the request to reading the response, in milliseconds.
        error: The connection error, if the request did not receive a response.
        served_by: The component that answered, as reported by the layer adapter.
    """

    method: str
    url: str
    status: int | None
    headers: dict[str, str]
    body: bytes
    elapsed_ms: float
    error: str | None = None
    served_by: str | None = None

    def json(self) -> Any:
        """Decode the response body as UTF-8 JSON.

        Returns:
            The decoded JSON value.
        """
        return json.loads(self.body.decode("utf-8"))


class RecordingClient:
    """HTTP client bound to one layer that appends every exchange to `exchanges.jsonl`.

    Args:
        run: The run directory that receives the exchange records.
        base_url: The base URL that request paths are appended to.
        token: The bearer token sent when a request has no Authorization header.
        timeout: The request timeout, in seconds.
        layer: The layer this client talks to, used by the adapter.
        adapter: The adapter for the layer's kind. Defaults to a plain `LayerAdapter`.
    """

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
        """Send a request once and record the exchange in the run directory.

        Connection errors are captured on the returned exchange rather than raised. The request
        is not retried.

        Args:
            method: The HTTP method.
            path: The path appended to the client's base URL.
            headers: Additional request headers, which override the adapter's headers.
            body: The raw request body.
            case_id: The ID of the case making the request, stored with the record.

        Returns:
            The recorded exchange.
        """
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
                    "request_headers": sent,  # append_jsonl redacts the header values.
                    "response_headers": exchange.headers,
                    "response_body": exchange.body[:4096].decode("utf-8", "replace"),
                    "error": exchange.error,
                }
            ],
        )
        return exchange

    def get(self, path: str, **kwargs) -> Exchange:
        """Send a GET request.

        Args:
            path: The path appended to the client's base URL.
            **kwargs: Keyword arguments passed to `request`.

        Returns:
            The recorded exchange.
        """
        return self.request("GET", path, **kwargs)

    def post_json(self, path: str, body: Any, **kwargs) -> Exchange:
        """Send a POST request with a JSON body.

        Args:
            path: The path appended to the client's base URL.
            body: The value to encode as the JSON request body.
            **kwargs: Keyword arguments passed to `request`. Any `headers` are merged over the
                JSON content type.

        Returns:
            The recorded exchange.
        """
        headers = {"content-type": "application/json", **kwargs.pop("headers", {})}
        return self.request("POST", path, body=json.dumps(body).encode(), headers=headers, **kwargs)


def sse_events(exchange: Exchange) -> list[str]:
    """Return the `data:` payloads of a Server-Sent Events response body, in order.

    Args:
        exchange: The exchange whose body is parsed.

    Returns:
        The payload of each `data:` line, including the final `[DONE]` marker if present.
    """
    text = exchange.body.decode("utf-8", "replace")
    return [line[len("data:") :].strip() for line in text.splitlines() if line.startswith("data:")]
