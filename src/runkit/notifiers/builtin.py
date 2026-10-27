"""Generic notifiers: JSON webhook, command, file, log. Plus the shared HTTP POST helper."""

from __future__ import annotations

import json
import shlex
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import retry
from ..log import emit
from ..redact import REDACTOR
from ..report import RunReport
from .base import NOTIFIERS, NotifierError

TIMEOUT_S = 10.0


def post_json(
    url: str,
    payload: Any,
    *,
    headers: dict[str, str] | None = None,
    policy: retry.RetryPolicy = retry.DEFAULT_POLICY,
    timeout: float = TIMEOUT_S,
) -> None:
    """POST JSON, retried within bounds on network errors, 429 and 5xx (honouring
    Retry-After up to the policy's cap). A 4xx other than 429 is final."""
    body = json.dumps(payload).encode("utf-8")
    sent = {"content-type": "application/json", **(headers or {})}

    def attempt() -> None:
        request = urllib.request.Request(url, data=body, headers=sent, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout):  # noqa: S310
                return
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                after = exc.headers.get("retry-after", "") if exc.headers else ""
                if after.replace(".", "", 1).isdigit():
                    raise retry.RetryAfter(float(after), f"HTTP {exc.code}") from exc
                raise OSError(f"HTTP {exc.code}") from exc
            raise NotifierError(f"refused: HTTP {exc.code}") from exc

    retry.call(
        attempt,
        what=f"POST {REDACTOR.scrub(url)}",
        policy=policy,
        retry_on=(urllib.error.URLError, OSError),
    )


@dataclass
class WebhookNotifier:
    """POSTs the whole RunReport as JSON (schema runkit.report/1): the integration point for
    any system that accepts a webhook (an incident tool, a chat bridge, your own service)."""

    url: str
    name: str = "webhook"
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.url.startswith(("https://", "http://")):
            raise NotifierError(f"webhook needs an http(s) URL, not {self.url!r}")
        REDACTOR.add(self.url)

    def send(self, report: RunReport) -> None:
        post_json(self.url, REDACTOR.value(report.to_dict()), headers=self.headers)


@dataclass
class CommandNotifier:
    """Runs a command with the report JSON on stdin: the escape hatch for any channel with a
    CLI (sendmail, a pager tool, a ticketing client). Non-zero exit is a failed delivery."""

    command: str
    name: str = "exec"
    timeout_s: float = 60.0

    def __post_init__(self) -> None:
        if not self.command.strip():
            raise NotifierError("exec: needs a command")

    def send(self, report: RunReport) -> None:
        payload = json.dumps(REDACTOR.value(report.to_dict()))
        done = subprocess.run(  # noqa: S603 -- the operator's own configured command
            shlex.split(self.command),
            input=payload,
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
            check=False,
        )
        if done.returncode != 0:
            raise NotifierError(f"{self.command!r} exited {done.returncode}: {done.stderr[:200]}")


@dataclass
class FileNotifier:
    """Appends the report as one JSON line: for a log shipper, or for tests."""

    path: str
    name: str = "file"

    def send(self, report: RunReport) -> None:
        target = Path(self.path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(REDACTOR.value(report.to_dict())) + "\n")


@dataclass
class LogNotifier:
    """The title and counts as primary output: a notifier for local runs."""

    target: str = ""
    name: str = "log"

    def send(self, report: RunReport) -> None:
        emit(report.title())
        emit(report.counts_line())


NOTIFIERS.register("webhook", WebhookNotifier)
NOTIFIERS.register("exec", CommandNotifier)
NOTIFIERS.register("file", FileNotifier)
NOTIFIERS.register("log", LogNotifier)
