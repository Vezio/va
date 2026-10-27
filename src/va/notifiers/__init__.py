"""Notifications: tell people (or systems) how a run ended, through any channel.

A notifier is anything with `name` and `send(report)`. It is chosen by a **spec** string,
`<kind>:<target>`, so the channel is configuration, not code:

    slack:env:SLACK_WEBHOOK          Slack incoming webhook; URL read from a variable
    webhook:https://hooks.example/x  the RunReport as JSON, for any system that takes a POST
    webhook:env:ALERTS_URL           the same, URL from a variable
    exec:/usr/local/bin/page-oncall  run a command with the report JSON on stdin
    file:/var/log/runs.jsonl         append the report as one JSON line
    log:                             the report's title and counts as primary output

`env:NAME` reads the target from a variable and registers it for redaction, because webhook
URLs are credentials. Several specs can be given; each is delivered independently and a
failure in one does not stop the others.

A new channel (Teams, Discord, email, PagerDuty, a ticketing system) is one class registered
under a kind, in code or through the `va.notifiers` entry point group:

    @PLUGINS.notifiers.register("teams")
    class TeamsNotifier:
        def __init__(self, target: str): self.url = target
        name = "teams"
        def send(self, report: RunReport) -> None: post_json(self.url, {...})
"""

from __future__ import annotations

from .base import (
    Delivery,
    Notifier,
    NotifierError,
    notify_all,
    parse_spec,
    parse_specs,
    resolve_target,
)
from .builtin import CommandNotifier, FileNotifier, LogNotifier, WebhookNotifier, post_json
from .slack import SlackNotifier

__all__ = [
    "CommandNotifier",
    "Delivery",
    "FileNotifier",
    "LogNotifier",
    "Notifier",
    "NotifierError",
    "SlackNotifier",
    "WebhookNotifier",
    "notify_all",
    "parse_spec",
    "parse_specs",
    "post_json",
    "resolve_target",
]
