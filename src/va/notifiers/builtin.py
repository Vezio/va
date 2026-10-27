"""General-purpose notifiers (webhook, command, file, log) and the shared JSON POST helper."""

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
from ..defaults import DEFAULTS, RetryPolicy
from ..log import emit
from ..plugins import PLUGINS
from ..redact import REDACTOR
from ..report import RunReport
from .base import NotifierError


def post_json(
    url: str,
    payload: Any,
    *,
    headers: dict[str, str] | None = None,
    policy: RetryPolicy = DEFAULTS.retry,
    timeout: float = DEFAULTS.http_timeout_s,
) -> None:
    """POST a JSON payload, retrying within bounds on transient failures.

    Network errors, HTTP 429 and HTTP 5xx responses are retried. A numeric Retry-After header
    is honoured up to the policy's cap. Any other 4xx response is treated as final.

    Args:
        url: The URL to post to.
        payload: A JSON-serialisable payload.
        headers: Extra request headers, added to the JSON content type.
        policy: The retry policy.
        timeout: The timeout in seconds for each attempt.

    Raises:
        NotifierError: If the server rejects the request with a non-retryable 4xx status.
    """
    body = json.dumps(payload).encode("utf-8")
    sent = {"content-type": "application/json", **(headers or {})}

    def attempt() -> None:
        """Send the request once, classifying the response for the retry loop.

        Raises:
            retry.RetryAfter: On a retryable status with a numeric Retry-After header.
            OSError: On a retryable status without a usable Retry-After header.
            NotifierError: On a non-retryable 4xx status.
        """
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
    """A notifier that POSTs the full RunReport as JSON (schema va.report/1).

    It suits any system that accepts a webhook, such as an incident tool, a chat bridge or an
    in-house service. The URL is registered with the redactor.

    Attributes:
        url: The http or https URL to post to.
        name: The notifier name.
        headers: Extra request headers.

    Raises:
        NotifierError: If the URL is not an http or https URL.
    """

    url: str
    name: str = "webhook"
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate the URL and register it as a secret.

        Raises:
            NotifierError: If the URL is not an http or https URL.
        """
        if not self.url.startswith(("https://", "http://")):
            raise NotifierError(f"webhook needs an http(s) URL, not {self.url!r}")
        REDACTOR.add(self.url)

    def send(self, report: RunReport) -> None:
        """POST the redacted report as JSON.

        Args:
            report: The run report to deliver.
        """
        post_json(self.url, REDACTOR.value(report.to_dict()), headers=self.headers)


@dataclass
class CommandNotifier:
    """A notifier that runs a command with the report JSON on its standard input.

    It supports any channel that has a command-line client, such as sendmail, a paging tool
    or a ticketing client. A non-zero exit status counts as a failed delivery.

    Attributes:
        command: The command line, split with shell quoting rules but not run in a shell.
        name: The notifier name.
        timeout_s: The time limit for the command in seconds.

    Raises:
        NotifierError: If the command is empty.
    """

    command: str
    name: str = "exec"
    timeout_s: float = DEFAULTS.command_timeout_s

    def __post_init__(self) -> None:
        """Validate that a command was given.

        Raises:
            NotifierError: If the command is empty.
        """
        if not self.command.strip():
            raise NotifierError("exec: needs a command")

    def send(self, report: RunReport) -> None:
        """Run the command with the redacted report JSON on standard input.

        Args:
            report: The run report to deliver.

        Raises:
            NotifierError: If the command exits with a non-zero status.
        """
        payload = json.dumps(REDACTOR.value(report.to_dict()))
        # The command is configured by the operator.
        done = subprocess.run(  # noqa: S603
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
    """A notifier that appends the report to a file as one JSON line.

    It is intended for log shippers and for tests.

    Attributes:
        path: The file to append to. `~` is expanded.
        name: The notifier name.
    """

    path: str
    name: str = "file"

    def send(self, report: RunReport) -> None:
        """Append the redacted report as a JSON line, creating parent directories.

        Args:
            report: The run report to deliver.
        """
        target = Path(self.path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(REDACTOR.value(report.to_dict())) + "\n")


@dataclass
class LogNotifier:
    """A notifier that writes the report title and counts as primary output.

    It is intended for local runs.

    Attributes:
        target: Unused. Accepted so that the notifier can be built from a `log:` spec.
        name: The notifier name.
    """

    target: str = ""
    name: str = "log"

    def send(self, report: RunReport) -> None:
        """Emit the report title and counts line.

        Args:
            report: The run report to deliver.
        """
        emit(report.title())
        emit(report.counts_line())


PLUGINS.notifiers.register("webhook", WebhookNotifier)
PLUGINS.notifiers.register("exec", CommandNotifier)
PLUGINS.notifiers.register("file", FileNotifier)
PLUGINS.notifiers.register("log", LogNotifier)
