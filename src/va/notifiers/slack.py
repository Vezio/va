"""A notifier for Slack incoming webhooks.

Slack is an ordinary implementation of the Notifier protocol. Formatting that only Slack
requires, such as escaping its markup characters, is kept in this module.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..defaults import DEFAULTS
from ..plugins import PLUGINS
from ..redact import REDACTOR
from ..report import RunReport
from .base import NotifierError
from .builtin import post_json


def escape(text: str) -> str:
    """Escape the characters Slack treats as markup (`&`, `<` and `>`).

    Args:
        text: The text to escape.

    Returns:
        The escaped text.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_text(report: RunReport, max_failures: int = DEFAULTS.max_failures_in_message) -> str:
    """Format a run report as plain Slack message text.

    The message lists the title and meaning, labels, counts, preflight problems, failures,
    regressions, any publication problem and links. Links are written as bare URLs, which all
    Slack clients render. The result is redacted.

    Args:
        report: The run report to format.
        max_failures: The maximum number of preflight problems and of failures to list.

    Returns:
        The message text.
    """
    lines = [escape(f"{report.title()}: {report.meaning}")]
    if report.outcome.labels:
        lines.append(escape(", ".join(f"{k}={v}" for k, v in report.outcome.labels.items())))
    if report.outcome.counts.total:
        lines.append(report.counts_line())
    for problem in report.preflight[:max_failures]:
        lines.append(escape(f"- preflight {problem.kind}: {problem.message}"))
    for failure in report.failures[:max_failures]:
        lines.append(escape(f"- {failure.key}: {failure.reason[:160]}"))
    if len(report.failures) > max_failures:
        lines.append(f"... and {len(report.failures) - max_failures} more")
    if report.comparison and report.comparison.regressions:
        lines.append(
            f"{len(report.comparison.regressions)} regression(s) since "
            f"{escape(', '.join(report.comparison.baseline_runs))}"
        )
    pub = report.publication
    if pub.reason and not pub.delivered:
        lines.append(escape(f"results not published: {pub.reason}"))
    for name, url in report.links.items():
        lines.append(f"{escape(name)}: {url}")
    return REDACTOR.scrub("\n".join(lines))


@dataclass
class SlackNotifier:
    """A notifier that posts a run summary to a Slack incoming webhook.

    Attributes:
        url: The https webhook URL. It is registered with the redactor.
        name: The notifier name.
        max_failures: The maximum number of failures to list in a message.

    Raises:
        NotifierError: If the URL is not an https URL.
    """

    url: str
    name: str = "slack"
    max_failures: int = DEFAULTS.max_failures_in_message

    def __post_init__(self) -> None:
        """Validate the webhook URL and register it as a secret.

        Raises:
            NotifierError: If the URL is not an https URL.
        """
        if not self.url.startswith("https://"):
            raise NotifierError("slack: needs an https webhook URL (use slack:env:VARIABLE)")
        REDACTOR.add(self.url)  # The webhook URL is itself a credential.

    def send(self, report: RunReport) -> None:
        """Post the formatted report to the webhook.

        Args:
            report: The run report to deliver.
        """
        post_json(self.url, {"text": format_text(report, self.max_failures)})


PLUGINS.notifiers.register("slack", SlackNotifier)
