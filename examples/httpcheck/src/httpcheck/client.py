"""HTTP client that records each request and response in the run directory.

The client uses the standard library rather than a service SDK, because an SDK can normalise
malformed bodies, unexpected statuses and headers that the cases need to observe. Requests are
not retried, so that a transient failure is reported rather than hidden.
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
    """One HTTP request and the response or error it produced.

    Attributes:
        method: HTTP method of the request.
        url: Full URL that was requested.
        status: HTTP status code, or None if no response was received.
        headers: Response headers.
        body: Raw response body.
        elapsed_ms: Time from sending the request to reading the response, in milliseconds.
        error: Description of the connection error, or None if a response was received.
    """

    method: str
    url: str
    status: int | None
    headers: dict[str, str]
    body: bytes
    elapsed_ms: float
    error: str | None = None

    def json(self) -> Any:
        """Decode the response body as UTF-8 JSON.

        Returns:
            The parsed JSON value.

        Raises:
            ValueError: If the body is not valid JSON or not valid UTF-8.
        """
        return json.loads(self.body.decode("utf-8"))


class RecordingClient:
    """HTTP client bound to one target that appends each exchange to `exchanges.jsonl`.

    Args:
        run: Run directory that receives the exchange records.
        base_url: Base URL that request paths are appended to.
        token: Bearer token sent in the Authorization header, unless the caller supplies one.
        timeout: Request timeout in seconds.
    """

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
        """Send a request and record the exchange in the run directory.

        HTTP error statuses and connection errors are returned in the exchange rather than
        raised, so that the calling case can assert on them.

        Args:
            method: HTTP method.
            path: Path appended to the base URL.
            headers: Request headers. An Authorization header here overrides the client token.
            body: Request body, or None for no body.
            case_id: Identifier of the calling case, stored with the record.

        Returns:
            The recorded exchange.
        """
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
                    "request_headers": sent,  # append_jsonl scrubs registered secrets.
                    "response_headers": exchange.headers,
                    "response_body": exchange.body[:4096].decode("utf-8", "replace"),
                    "error": exchange.error,
                }
            ],
        )
        return exchange

    def get(self, path: str, **kwargs) -> Exchange:
        """Send a GET request and record the exchange.

        Args:
            path: Path appended to the base URL.
            **kwargs: Keyword arguments passed to `request`.

        Returns:
            The recorded exchange.
        """
        return self.request("GET", path, **kwargs)
