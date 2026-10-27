"""Slack incoming webhooks: one implementation of the Notifier protocol, nothing more special
than the others. Formatting that only Slack needs (escaping, mrkdwn) lives here."""

from __future__ import annotations

from dataclasses import dataclass

from ..redact import REDACTOR
from ..report import RunReport
from .base import NOTIFIERS, NotifierError
from .builtin import post_json


def escape(text: str) -> str:
    """Slack treats &, < and > as markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_text(report: RunReport, max_failures: int = 10) -> str:
    """Plain lines; links as bare URLs, which every Slack client renders."""
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
    url: str
    name: str = "slack"
    max_failures: int = 10

    def __post_init__(self) -> None:
        if not self.url.startswith("https://"):
            raise NotifierError("slack: needs an https webhook URL (use slack:env:VARIABLE)")
        REDACTOR.add(self.url)  # the webhook URL is itself a credential

    def send(self, report: RunReport) -> None:
        post_json(self.url, {"text": format_text(report, self.max_failures)})


NOTIFIERS.register("slack", SlackNotifier)
