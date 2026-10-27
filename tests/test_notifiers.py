"""Notifiers: specs, built-ins, plugins, and delivery isolation."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from va.compare import compare
from va.exit_codes import ExitCode
from va.model import ResultRecord, RunOutcome, Verdict
from va.notifiers import (
    NotifierError,
    SlackNotifier,
    WebhookNotifier,
    notify_all,
    parse_spec,
    parse_specs,
)
from va.notifiers.slack import format_text
from va.plugins import PLUGINS
from va.redact import REDACTOR
from va.report import RunReport


@pytest.fixture(autouse=True)
def _clean():
    yield
    REDACTOR.clear()


@pytest.fixture
def receiver():
    """A local HTTP endpoint that records what was POSTed; `status` sets its answer."""
    received: list[dict] = []
    state = {"status": 200}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["content-length"]))
            received.append(json.loads(body))
            self.send_response(state["status"])
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/hook", received, state
    server.shutdown()


def _report(code=ExitCode.FAILURES) -> RunReport:
    outcome = RunOutcome(run_id="r1", tool="tool", subject={"name": "svc"}, labels={"t": "n"})
    records = [ResultRecord("r1", "health.status", Verdict.FAIL, target="gw", reason="500 <x>")]
    outcome.record(records)
    outcome.finish(code)
    baseline = [ResultRecord("r0", "health.status", Verdict.PASS, target="gw")]
    return RunReport.build(
        outcome, compare(records, [("r0", baseline)], "r1"), {"ci": "https://ci.example/1"}
    )


def test_report_is_one_source_for_every_channel():
    report = _report()
    assert report.headline == "FAILED" and report.comparison.regressions
    assert "health.status" in report.markdown() and "regression" in report.markdown()
    assert report.to_dict()["schema"] == "va.report/1"


def test_webhook_posts_the_report_as_json(receiver):
    url, received, _ = receiver
    parse_spec(f"webhook:{url}").send(_report())
    assert received[0]["headline"] == "FAILED" and received[0]["outcome"]["counts"]["failed"] == 1


def test_target_from_env_is_registered_as_a_secret(receiver, monkeypatch):
    url, received, _ = receiver
    monkeypatch.setenv("HOOK_URL", url)
    notifier = parse_spec("webhook:env:HOOK_URL")
    assert REDACTOR.scrub(f"posting to {url}") == "posting to ***REDACTED***"
    notifier.send(_report())
    assert received


def test_slack_formats_and_escapes():
    text = format_text(_report())
    assert "FAILED" in text and "&lt;x&gt;" in text and "https://ci.example/1" in text
    with pytest.raises(NotifierError, match="https"):
        SlackNotifier("http://insecure")


def test_one_failing_channel_does_not_stop_the_others(receiver):
    url, received, state = receiver
    state["status"] = 400  # a 4xx is final: no retry
    failing = WebhookNotifier(url, name="bad")
    deliveries = notify_all([failing, parse_spec("log:")], _report())
    assert [d.ok for d in deliveries] == [False, True] and "HTTP 400" in deliveries[0].error


def test_exec_and_file_notifiers(tmp_path):
    out = tmp_path / "out.json"
    script = f"{sys.executable} -c \"import sys; open({str(out)!r}, 'w').write(sys.stdin.read())\""
    parse_spec(f"exec:{script}").send(_report())
    assert json.loads(out.read_text())["run_id"] == "r1"
    log_path = tmp_path / "runs.jsonl"
    parse_spec(f"file:{log_path}").send(_report())
    assert json.loads(log_path.read_text().splitlines()[0])["headline"] == "FAILED"
    with pytest.raises(NotifierError):
        parse_spec("exec:false").send(_report())


def test_a_new_channel_is_one_registration():
    sent = []

    class Pager:
        name = "pager"

        def __init__(self, target):
            self.target = target

        def send(self, report):
            sent.append((self.target, report.headline))

    PLUGINS.notifiers.register("pager", Pager, replace=True)
    for notifier in parse_specs(["pager:team-a,log:"]):
        notifier.send(_report())
    assert sent == [("team-a", "FAILED")]


@pytest.mark.parametrize("spec", ["nonsense", "nope:x", "webhook:not-a-url", "webhook:env:UNSET_X"])
def test_bad_specs_are_refused(spec):
    with pytest.raises(NotifierError):
        parse_spec(spec)
