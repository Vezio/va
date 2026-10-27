"""End to end against the fake service: exit codes, evidence, publication, and each case
proven to fail against its defect."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cli_framework import ExitCode
from cli_framework.redact import REDACTOR
from fake_server import FakeService

from httpcheck.cli import build_app

PROFILE = """
name: fake
targets:
  - name: svc
    base_url_env: SVC_URL
    key_env: SVC_TOKEN
    capabilities: [health, json, auth]
    max_latency_ms: 300
"""


@pytest.fixture
def service(monkeypatch):
    fake = FakeService()
    monkeypatch.setenv("SVC_URL", fake.url)
    monkeypatch.setenv("SVC_TOKEN", fake.token)
    yield fake
    fake.close()
    REDACTOR.clear()


@pytest.fixture
def profile(tmp_path) -> Path:
    path = tmp_path / "fake.yaml"
    path.write_text(PROFILE, encoding="utf-8")
    return path


def run(profile: Path, tmp_path: Path, *extra: str) -> tuple[int, Path]:
    run_dir = tmp_path / "run"
    code = build_app().main(["run", "--profile", str(profile), "--run-dir", str(run_dir), *extra])
    return code, run_dir


def results(run_dir: Path) -> dict[str, dict]:
    lines = (run_dir / "results.jsonl").read_text().splitlines()
    return {f"{r['case_id']}[{r['variant']}]": r for r in map(json.loads, lines)}


def test_healthy_service_passes_with_evidence(service, profile, tmp_path):
    code, run_dir = run(profile, tmp_path)
    assert code == ExitCode.OK
    records = results(run_dir)
    assert all(r["verdict"] == "pass" for r in records.values())
    assert all(r["checks"] for r in records.values()), "a pass must carry its checks"
    outcome = json.loads((run_dir / "outcome.json").read_text())
    assert outcome["exit_label"] == "ok" and outcome["counts"]["passed"] == len(records)
    assert (run_dir / "exchanges.jsonl").is_file() and (run_dir / "summary.md").is_file()


def test_token_never_reaches_a_file(service, profile, tmp_path):
    code, run_dir = run(profile, tmp_path, "--log-level", "DEBUG")
    assert code == ExitCode.OK
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert service.token not in path.read_text(errors="replace"), path.name


@pytest.mark.parametrize(
    "defect, value, case",
    [
        ("health_status", 503, "health.status[]"),
        ("health_delay_s", 0.5, "health.latency[]"),
        ("health_content_type", "text/html", "api.json_content_type[]"),
        ("health_body", b"<html>", "api.json_content_type[]"),
        ("unknown_path_status", 200, "api.unknown_path[GET]"),
        ("unknown_path_status", 500, "api.unknown_path[POST]"),
        ("accept_any_key", True, "auth.rejects_bad_key[]"),
    ],
)
def test_each_case_fails_against_its_defect(service, profile, tmp_path, defect, value, case):
    setattr(service.defects, defect, value)
    code, run_dir = run(profile, tmp_path)
    assert code == ExitCode.FAILURES
    record = results(run_dir)[case]
    assert record["verdict"] == "fail"
    assert any(not c["ok"] for c in record["checks"]), "the deciding check must be recorded"


def test_dead_target_is_preflight_not_a_failure(monkeypatch, profile, tmp_path):
    monkeypatch.setenv("SVC_URL", "http://127.0.0.1:9")  # nothing listens on the discard port
    monkeypatch.setenv("SVC_TOKEN", "whatever-token")
    code, run_dir = run(profile, tmp_path)
    assert code == ExitCode.PREFLIGHT
    assert not (run_dir / "results.jsonl").exists(), "nothing may be tested after preflight fails"
    outcome = json.loads((run_dir / "outcome.json").read_text())
    assert outcome["preflight"][0]["kind"] == "reachability"


def test_missing_credential_is_preflight(monkeypatch, service, profile, tmp_path):
    monkeypatch.delenv("SVC_TOKEN")
    code, _ = run(profile, tmp_path)
    assert code == ExitCode.PREFLIGHT


def test_invalid_profile_is_usage(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\ntargets:\n  - name: a\n    base_url_env: lower\n")
    assert build_app().main(["run", "--profile", str(bad), "--run-dir", str(tmp_path / "r")]) == 2


def test_publish_to_file_writes_manifest_and_archive_is_comparable(service, profile, tmp_path):
    archive = tmp_path / "archive"
    code, first = run(profile, tmp_path, "--remote-output", f"file://{archive}")
    assert code == ExitCode.OK
    outcome = json.loads((first / "outcome.json").read_text())
    assert outcome["publication"]["status"] == "published"
    published = archive / "fake" / first.name
    assert (published / "MANIFEST.json").is_file()

    service.defects.health_status = 500
    second = tmp_path / "run2"
    assert build_app().main(["run", "--profile", str(profile), "--run-dir", str(second)]) == 1
    code = build_app().main(
        [
            "compare",
            str(second),
            "--remote-output",
            f"file://{archive}",
            "--group",
            "fake",
            "--baseline-label",
            "",
            "--fail-on-regression",
        ]
    )
    assert code == ExitCode.FAILURES
    comparison = json.loads((second / "comparison.json").read_text())
    regressions = {c["key"]["case_id"] for c in comparison["changes"] if c["kind"] == "regression"}
    assert "health.status" in regressions


def test_unpublishable_destination_is_preflight(service, profile, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    code, _ = run(profile, tmp_path, "--remote-output", f"file://{blocker}/x")
    assert code == ExitCode.PREFLIGHT


def test_job_runs_child_publishes_and_notifies(service, profile, tmp_path):
    archive = tmp_path / "archive"
    env = dict(
        os.environ,
        HTTPCHECK_NOTIFY=f"webhook:{service.url}/webhook",
        HTTPCHECK_RESULTS_URI=f"file://{archive}",
        HTTPCHECK_GROUP="fake",
    )
    done = subprocess.run(
        [
            str(Path(sys.executable).parent / "httpcheck"),
            "job",
            "--work-dir",
            str(tmp_path / "w"),
            "--profile",
            str(profile),
            "--label",
            "trigger=nightly",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == ExitCode.OK, done.stderr
    runs = list((archive / "fake").iterdir())
    assert len(runs) == 1 and (runs[0] / "MANIFEST.json").is_file()
    payloads = service.webhook_payloads()
    assert len(payloads) == 1, "the child must not notify as well"
    assert payloads[0]["headline"] == "PASSED"
    assert service.token not in json.dumps(payloads[0])


def test_job_stops_an_overrunning_run_and_still_publishes(service, profile, tmp_path):
    service.defects.health_delay_s = 3  # every health case now takes 3s
    archive = tmp_path / "archive"
    env = dict(os.environ, HTTPCHECK_RESULTS_URI=f"file://{archive}")
    done = subprocess.run(
        [
            str(Path(sys.executable).parent / "httpcheck"),
            "job",
            "--work-dir",
            str(tmp_path / "w"),
            "--budget",
            "2",
            "--grace",
            "20",
            "--profile",
            str(profile),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == ExitCode.INTERRUPTED, done.stderr
    published = list((archive / "fake").iterdir())
    assert len(published) == 1 and (published[0] / "MANIFEST.json").is_file()
    outcome = json.loads((published[0] / "outcome.json").read_text())
    assert outcome["exit_label"] == "interrupted"


def test_opt_in_flags_end_to_end(service, profile, tmp_path):
    junit = tmp_path / "ci/junit.xml"
    metrics = tmp_path / "textfile/httpcheck.prom"
    code, run_dir = run(
        profile,
        tmp_path,
        "--workers",
        "4",
        "--case-timeout",
        "10",
        "--html",
        "--junit",
        str(junit),
        "--metrics-file",
        str(metrics),
    )
    assert code == ExitCode.OK
    assert (run_dir / "report.html").is_file() and (run_dir / "junit.xml").is_file()
    assert junit.read_text().startswith("<?xml")
    assert 'httpcheck_run_exit_code{group="fake",tool="httpcheck"} 0' in metrics.read_text()
    assert service.token not in (run_dir / "report.html").read_text()
    # Concurrent cases appended to the shared exchanges file without tearing a line.
    for line in (run_dir / "exchanges.jsonl").read_text().splitlines():
        json.loads(line)


@pytest.mark.parametrize("flag", [["--workers", "0"], ["--case-timeout", "0"]])
def test_bad_opt_in_values_are_usage_errors(service, profile, tmp_path, flag):
    code, run_dir = run(profile, tmp_path, *flag)
    assert code == ExitCode.USAGE
    assert not run_dir.exists(), "a usage error must not leave a run directory behind"


def test_job_gives_its_run_a_shorter_budget_than_its_own(service, profile, tmp_path):
    """The run stops starting cases --grace seconds before the job would stop it, so it ends
    on its own and writes its outcome; the environment's budget does not reach the child."""
    env = dict(os.environ, HTTPCHECK_RUN_BUDGET="999")
    done = subprocess.run(
        [
            str(Path(sys.executable).parent / "httpcheck"),
            "job",
            "--work-dir",
            str(tmp_path / "w"),
            "--budget",
            "100",
            "--grace",
            "30",
            "--profile",
            str(profile),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == ExitCode.OK, done.stderr
    assert "--budget 70" in done.stderr  # 100 - 30, logged with the child's command line


def test_primary_output_piped_into_a_reader_that_stops_early_is_quiet():
    """`httpcheck profile show ... | head -1` prints one line and no traceback."""
    profile = Path(__file__).parent.parent / "profiles" / "example.yaml"
    done = subprocess.run(
        f"{Path(sys.executable).parent / 'httpcheck'} profile show {profile} | head -1",
        shell=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.stdout.startswith("gateway") and done.stdout.count("\n") == 1
    assert done.stderr == "", done.stderr
