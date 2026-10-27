"""Notifications that report how a run ended, to people or to other systems.

A notifier is any object with a `name` attribute and a `send(report)` method. It is selected
by a spec string of the form `<kind>:<target>`, so the channel is a matter of configuration:

    slack:env:SLACK_WEBHOOK          Slack incoming webhook; URL read from a variable
    webhook:https://hooks.example/x  the RunReport as JSON, for any system that takes a POST
    webhook:env:ALERTS_URL           the same, URL from a variable
    exec:/usr/local/bin/page-oncall  run a command with the report JSON on stdin
    file:/var/log/runs.jsonl         append the report as one JSON line
    log:                             the report's title and counts as primary output

A target of the form `env:NAME` is read from the named environment variable and registered
for redaction, because webhook URLs are credentials. Several specs can be given. Each is
delivered independently, and a failure in one does not prevent delivery to the others.

A new channel (Teams, Discord, email, PagerDuty, a ticketing system) is a class registered
under a kind, either in code or through the `va.notifiers` entry point group:

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
