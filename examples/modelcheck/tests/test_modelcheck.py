"""modelcheck against the fake stack: a healthy stack passes; each defect fails the case that
exists to catch it, on the layer that has it, and only there."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cli_framework import ExitCode
from cli_framework.redact import REDACTOR
from fake_stack import TOKEN, FakeStack

from modelcheck.cli import build_app

PROFILE = Path(__file__).parent.parent / "profiles" / "stack.toml"


@pytest.fixture
def stack_env(monkeypatch):
    stacks: list[FakeStack] = []

    def start(*defects: str) -> FakeStack:
        stack = FakeStack(set(defects))
        stacks.append(stack)
        monkeypatch.setenv("GATEWAY_URL", stack.gateway_url)
        monkeypatch.setenv("ENGINE_URL", stack.engine_url)
        monkeypatch.setenv("GATEWAY_TOKEN", TOKEN)
        return stack

    yield start
    for stack in stacks:
        stack.close()
    REDACTOR.clear()


def run(tmp_path: Path, *extra: str, name: str = "run") -> tuple[int, Path]:
    run_dir = tmp_path / name
    code = build_app().main(["run", "--profile", str(PROFILE), "--run-dir", str(run_dir), *extra])
    return code, run_dir


def verdicts(run_dir: Path) -> dict[tuple[str, str], str]:
    """(target, case[variant]) -> verdict."""
    out = {}
    for line in (run_dir / "results.jsonl").read_text().splitlines():
        r = json.loads(line)
        key = r["case_id"] + (f"[{r['variant']}]" if r["variant"] else "")
        out[(r["target"], key)] = r["verdict"]
    return out


def test_a_healthy_stack_passes_and_auth_skips_on_the_engine(stack_env, tmp_path):
    stack_env()
    code, run_dir = run(tmp_path)
    assert code == ExitCode.OK
    v = verdicts(run_dir)
    assert len(v) == 2 * 8  # 7 cases (one with two variants) on two layers
    skipped = {key for key, verdict in v.items() if verdict == "skip"}
    assert skipped == {("engine", "auth.rejects_bad_key")}  # the engine does not declare auth
    assert all(verdict == "pass" for key, verdict in v.items() if key not in skipped)
    assert TOKEN not in (run_dir / "exchanges.jsonl").read_text()


@pytest.mark.parametrize(
    "defect, layer, case",
    [
        ("gateway_drops_done", "gateway", "stream.well_formed"),
        ("gateway_error_status", "gateway", "errors.unknown_model"),
        ("gateway_any_key", "gateway", "auth.rejects_bad_key"),
        ("engine_empty_content", "engine", "chat.completion[user_only]"),
        ("engine_bad_usage", "engine", "chat.usage_adds_up"),
        ("engine_bare_errors", "engine", "errors.unknown_model"),
    ],
)
def test_each_defect_fails_its_case_on_its_layer(stack_env, tmp_path, defect, layer, case):
    stack_env(defect)
    code, run_dir = run(tmp_path)
    assert code == ExitCode.FAILURES
    v = verdicts(run_dir)
    assert v[(layer, case)] == "fail"
    if defect.startswith("gateway"):
        other = ("engine", case)
        # The engine is healthy: its column passes (or skips, for auth) the same case.
        assert v.get(other, "skip") in ("pass", "skip"), "a gateway defect leaked into the engine"


def test_latency_is_judged_against_each_layers_own_budget(stack_env, tmp_path):
    """The engine takes 1s: over its 800ms budget, within the gateway's 1500ms."""
    stack_env("engine_slow")
    code, run_dir = run(tmp_path, "-k", "chat.latency")
    v = verdicts(run_dir)
    assert code == ExitCode.FAILURES
    assert v == {("engine", "chat.latency"): "fail", ("gateway", "chat.latency"): "pass"}


def test_comparison_names_the_regression_and_its_layer(stack_env, tmp_path):
    archive = f"file://{tmp_path / 'archive'}"
    stack_env()
    code, _ = run(tmp_path, "--remote-output", archive, "--label", "trigger=nightly", name="night1")
    assert code == ExitCode.OK
    stack_env("gateway_drops_done")
    code, run_dir = run(tmp_path, "--remote-output", archive, "--compare", name="night2")
    assert code == ExitCode.FAILURES
    changes = json.loads((run_dir / "comparison.json").read_text())["changes"]
    regressions = {
        (c["key"]["target"], c["key"]["case_id"]) for c in changes if c["kind"] == "regression"
    }
    assert regressions == {("gateway", "stream.well_formed")}


def test_workers_keep_exclusive_cases_alone_and_results_complete(stack_env, tmp_path):
    stack_env()
    code, run_dir = run(tmp_path, "--workers", "4")
    assert code == ExitCode.OK and len(verdicts(run_dir)) == 16


def test_preflight_names_an_unreachable_layer(monkeypatch, tmp_path):
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ENGINE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("GATEWAY_TOKEN", TOKEN)
    code, run_dir = run(tmp_path)
    assert code == ExitCode.PREFLIGHT
    problems = json.loads((run_dir / "outcome.json").read_text())["preflight"]
    assert {p["kind"] for p in problems} == {"reachability"}


def test_a_broken_profile_reports_every_problem_and_exits_2(tmp_path):
    from cli_framework import ProfileError

    from modelcheck.profile import load

    broken = tmp_path / "broken.toml"
    broken.write_text(
        'name = "x"\nmodle = "m"\n[[targets]]\nname = "g"\nbase_url_env = "G_URL"\n'
        'capabilites = ["chat"]\ncapabilities = ["chats"]\n'
    )
    with pytest.raises(ProfileError) as exc:
        load(broken)
    assert exc.value.problems == [
        "modle: is not a known key; did you mean 'model'?",
        "targets[0].capabilites: is not a known key; did you mean 'capabilities'?",
        "targets[0].capabilities: must be one of models, chat, stream, auth, not 'chats'",
        "targets[0].model: is required (here or at the top of the profile)",
    ]
    assert build_app().main(["profile", "validate", str(broken)]) == ExitCode.USAGE
