"""Tests for opt-in features: concurrency, case timeouts, report outputs and S3.

The S3 tests run against a mock.
"""

from __future__ import annotations

import os
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import pytest

from va.hooks import RunHooks
from va.model import Check, ResultRecord, RunOutcome, Verdict
from va.outputs import HtmlReport, JUnitReport, MetricsFile, render_html, render_junit
from va.outputs.metrics import render_metrics
from va.publish import Archive, deliver
from va.redact import REDACTOR
from va.rundir import RunDir
from va.runner import BudgetExceeded, CaseRegistry, Runner


@pytest.fixture(autouse=True)
def _clean():
    """Clear the process-wide redactor after each test."""
    yield
    REDACTOR.clear()


@dataclass(frozen=True)
class T:
    """Minimal target used by the runner tests.

    Attributes:
        name: Target name.
        capabilities: Capabilities the target declares.
    """

    name: str
    capabilities: frozenset[str] = frozenset()


class Gauge:
    """Thread-safe counter of cases in flight and the peak number in flight.

    Attributes:
        lock: Lock guarding the counters.
        now: Number of cases currently in flight.
        peak: Highest number of cases in flight at once.
        exclusive_overlap: Whether an exclusive case ran alongside another case.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.now = self.peak = 0
        self.exclusive_overlap = False

    def __enter__(self):
        """Count a case as started and update the peak."""
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
        return self

    def __exit__(self, *exc):
        """Count a case as finished."""
        with self.lock:
            self.now -= 1


def test_workers_run_cases_concurrently_and_exclusive_ones_alone(tmp_path):
    """Verify that workers run cases concurrently and exclusive cases alone."""
    reg, gauge = CaseRegistry(), Gauge()
    hook_threads: set[str] = set()

    class Where(RunHooks):
        """Hooks that record the thread each result is reported on."""

        def on_result(self, run, record):
            """Record the name of the current thread."""
            hook_threads.add(threading.current_thread().name)

    for i in range(8):

        @reg.case(f"shared.{i}")
        def shared(ctx):
            """Sleep while counted as in flight."""
            with gauge:
                time.sleep(0.15)
            ctx.check("slept", True, True, True)

    @reg.case("cache.alone", exclusive=True)
    def alone(ctx):
        """Check that no other case is in flight."""
        with gauge:
            gauge.exclusive_overlap = gauge.now > 1
            ctx.check("alone", gauge.now, 1, gauge.now == 1)

    run = RunDir.create(tmp_path)
    started = time.monotonic()
    records = Runner(run, workers=4, hooks=Where()).run_all(reg.select(), [T("t")])
    elapsed = time.monotonic() - started
    assert gauge.peak >= 3, "cases did not overlap"
    assert elapsed < 8 * 0.15, "no speed-up from 4 workers"
    assert not gauge.exclusive_overlap
    # Results are in plan order.
    assert [r.case_id for r in records] == [c.case_id for c in reg.select()]
    assert len(run.results()) == 9 and all(r.verdict is Verdict.PASS for r in records)
    assert hook_threads == {threading.main_thread().name}  # Hooks run only on the runner's thread.


def test_one_worker_is_the_default_and_strictly_serial(tmp_path):
    """Verify that the runner defaults to one worker and runs cases serially."""
    reg, gauge = CaseRegistry(), Gauge()
    for i in range(3):
        reg.case(f"c.{i}")(lambda ctx: (gauge.__enter__(), time.sleep(0.02), gauge.__exit__()))
    Runner(RunDir.create(tmp_path)).run_all(reg.select(), [T("t")])
    assert gauge.peak == 1


def test_budget_stops_new_cases_in_parallel(tmp_path):
    """Verify that an exhausted budget stops new cases from starting with several workers."""
    reg = CaseRegistry()
    for i in range(20):
        reg.case(f"slow.{i}")(lambda ctx: time.sleep(0.1))
    run = RunDir.create(tmp_path)
    runner = Runner(run, workers=2)
    runner.deadline = runner.clock() + 0.25
    with pytest.raises(BudgetExceeded):
        runner.run_all(reg.select(), [T("t")])
    finished = len(run.results())
    assert 0 < finished < 20  # Finished cases are on disk and the others did not start.


def test_a_hung_case_is_abandoned_as_an_error(tmp_path):
    """Verify that a case that exceeds its timeout is abandoned as an error.

    Evidence the abandoned case writes afterwards is discarded.
    """
    reg, release = CaseRegistry(), threading.Event()

    @reg.case("hangs")
    def hangs(ctx):
        """Block until released, then write evidence."""
        release.wait(5)
        ctx.save("late.txt", "written after the timeout")

    @reg.case("quick", timeout_s=5)
    def quick(ctx):
        """Pass with one check under its own timeout."""
        ctx.check("fine", 1, 1, True)

    run = RunDir.create(tmp_path)
    started = time.monotonic()
    records = Runner(run, case_timeout_s=0.2).run_all(reg.select(), [T("t")])
    assert time.monotonic() - started < 2
    by_id = {r.case_id: r for r in records}
    assert by_id["hangs"].verdict is Verdict.ERROR and "0.2s" in by_id["hangs"].reason
    assert by_id["quick"].verdict is Verdict.PASS  # The case's own timeout overrides the runner's.
    release.set()
    time.sleep(0.1)
    assert not (run.path / "evidence/t/hangs/late.txt").exists(), "abandoned evidence kept"


def _sample(run: RunDir) -> RunOutcome:
    """Add one result of each verdict to a run and return the finished outcome.

    Args:
        run: Run directory to add the results to.

    Returns:
        The run outcome, finished with exit code 1.
    """
    records = [
        ResultRecord(
            run.run_id,
            "health.status",
            Verdict.PASS,
            target="gw",
            checks=[Check("http_status", 200, 200, True)],
        ),
        ResultRecord(
            run.run_id,
            "api.<script>",
            Verdict.FAIL,
            target="gw",
            variant="POST",
            reason="status: expected 404, got 500 key=topsecret-1234",
            checks=[Check("http_status", 500, 404, False)],
        ),
        ResultRecord(
            run.run_id,
            "auth.key",
            Verdict.SKIP,
            target="engine",
            skip_class="undeclared",
            reason="engine does not declare auth",
        ),
        ResultRecord(run.run_id, "boom", Verdict.ERROR, target="engine", reason="ValueError: x"),
    ]
    for r in records:
        run.add_result(r)
    outcome = RunOutcome(run_id=run.run_id, tool="tool", group="g", labels={"trigger": "nightly"})
    outcome.record(records)
    outcome.finish(1)
    return outcome


def test_html_report_is_escaped_redacted_and_self_contained(tmp_path):
    """Verify that the HTML report is escaped, redacted and loads no external resources."""
    REDACTOR.add("topsecret-1234")
    run = RunDir.create(tmp_path)
    outcome = _sample(run)
    HtmlReport(copy_to=tmp_path / "copy.html").on_finish(run, outcome)
    page = run.file("report.html").read_text()
    assert "<script>" not in page and "api.&lt;script&gt;" in page
    assert (
        "topsecret-1234" not in page
        and "topsecret-1234" not in (tmp_path / "copy.html").read_text()
    )
    assert "http://" not in page and "https://" not in page  # The page loads no external resources.
    assert "undeclared" in page and "FAILED" in page
    assert render_html(outcome, run.results()).count("<tr>") >= 5


def test_junit_is_valid_and_classifies_every_verdict(tmp_path):
    """Verify that the JUnit report is valid XML and classifies every verdict."""
    run = RunDir.create(tmp_path)
    outcome = _sample(run)
    JUnitReport(copy_to=tmp_path / "out/junit.xml").on_finish(run, outcome)
    root = ET.fromstring((tmp_path / "out/junit.xml").read_text().split("?>", 1)[1])
    suites = {s.get("name"): s for s in root}
    assert suites["gw"].get("failures") == "1" and suites["engine"].get("errors") == "1"
    assert suites["engine"].get("skipped") == "1"
    failure = root.find(".//testcase[@name='api.<script>[POST]']/failure")
    assert failure is not None and failure.get("type") == "http_status"
    assert (
        "NOT OK http_status" in root.find(".//testcase[@name='api.<script>[POST]']/system-out").text
    )
    assert render_junit(outcome, run.results()).startswith("<?xml")


def test_metrics_file_is_prometheus_text_and_atomic(tmp_path):
    """Verify that the metrics file is Prometheus text and is written atomically."""
    run = RunDir.create(tmp_path)
    outcome = _sample(run)
    target = tmp_path / "textfile/tool.prom"
    hook = MetricsFile(target, prefix="mytool")
    hook.on_finish(run, outcome)
    text = target.read_text()
    assert 'mytool_run_exit_code{group="g",tool="tool"} 1' in text
    assert 'mytool_cases{group="g",target="gw",tool="tool",verdict="fail"} 1' in text
    assert 'mytool_skips{group="g",skip_class="undeclared",tool="tool"} 1' in text
    assert "# TYPE mytool_run_duration_seconds gauge" in text
    assert not list(target.parent.glob(".tool.prom.*")), "temporary file left behind"
    assert run.file("metrics.prom").read_text() == text
    assert render_metrics(outcome, run.results()).startswith("# HELP va_run_exit_code")


@pytest.fixture
def s3(monkeypatch):
    """Provide a mocked S3 client with a results bucket.

    The test is skipped when moto or boto3 is not installed.

    Yields:
        A boto3 S3 client inside the mock.
    """
    moto = pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing-key-id",
        "AWS_SECRET_ACCESS_KEY": "testing-secret-key",
        "AWS_DEFAULT_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL_S3", raising=False)
    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="results")
        yield client


def test_s3_publish_manifest_last_and_archive_reads_back(s3, tmp_path):
    """Verify that S3 publishing completes and the archive lists only complete runs."""
    runs = []
    for verdict in (Verdict.PASS, Verdict.FAIL):
        run = RunDir.create(tmp_path / "w")
        run.add_result(ResultRecord(run.run_id, "c", verdict, target="t"))
        outcome = RunOutcome(run_id=run.run_id, tool="t", labels={"trigger": "nightly"})
        run.save_outcome(outcome)
        publication = deliver(run, "s3://results/nightly", "grp")
        assert publication.delivered, publication.reason
        assert publication.location == f"s3://results/nightly/grp/{run.run_id}/"
        runs.append(run.run_id)
        time.sleep(1.1)  # Run identifiers sort by second.
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="results")["Contents"]]
    assert f"nightly/grp/{runs[0]}/MANIFEST.json" in keys
    # A run whose manifest never arrived is not listed.
    s3.put_object(Bucket="results", Key="nightly/grp/9999-partial/results.jsonl", Body=b"{}\n")
    archive = Archive.at("s3://results/nightly")
    assert archive.runs("grp") == runs
    baselines = archive.baselines("grp", exclude=runs[1], label="trigger=nightly")
    assert [rid for rid, _ in baselines] == [runs[0]]
    assert baselines[0][1][0].verdict is Verdict.PASS
    # The credential in use, which moto substitutes inside the mock, is registered
    # as a secret.
    secret = os.environ["AWS_SECRET_ACCESS_KEY"]
    assert REDACTOR.scrub(f"x {secret}") == "x ***REDACTED***"


def test_s3_write_check_and_missing_bucket(s3, tmp_path):
    """Verify that the write check passes for an existing bucket and fails for a missing one."""
    from va.preflight import destination_writable

    assert list(destination_writable("s3://results/p")()) == []
    problems = list(destination_writable("s3://no-such-bucket/p")())
    assert problems and problems[0].kind == "output"
