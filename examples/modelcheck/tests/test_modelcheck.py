"""modelcheck against the fake stack: a healthy stack passes; each defect fails the case that
exists to catch it on the layer that has it and the layers in front of it, never behind it, and
the run says which layer it starts at."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fake_stack import TOKEN, FakeStack
from va import ExitCode
from va.redact import REDACTOR

from modelcheck.cli import build_app

PROFILE = Path(__file__).parent.parent / "profiles" / "stack.yaml"
LAYERS = ("gateway", "router", "engine")  # outermost first
BEHIND = {"gateway": ("router", "engine"), "router": ("engine",), "engine": ()}


@pytest.fixture
def stack_env(monkeypatch):
    stacks: list[FakeStack] = []

    def start(*defects: str) -> FakeStack:
        stack = FakeStack(set(defects))
        stacks.append(stack)
        monkeypatch.setenv("GATEWAY_URL", stack.gateway_url)
        monkeypatch.setenv("ROUTER_URL", stack.router_url)
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


def located(run_dir: Path) -> set[tuple[str, str, bool]]:
    """(case[variant], layer, confirmed) for each failure start in layers.json."""
    data = json.loads((run_dir / "layers.json").read_text())
    return {
        (f["case_id"] + (f"[{f['variant']}]" if f["variant"] else ""), f["layer"], f["confirmed"])
        for f in data["failures"]
    }


def test_a_healthy_stack_passes_and_auth_skips_behind_the_gateway(stack_env, tmp_path):
    stack_env()
    code, run_dir = run(tmp_path)
    assert code == ExitCode.OK
    v = verdicts(run_dir)
    assert len(v) == 3 * 8  # 7 cases (one with two variants) on three layers
    skipped = {key for key, verdict in v.items() if verdict == "skip"}
    # Only the gateway declares auth.
    assert skipped == {("router", "auth.rejects_bad_key"), ("engine", "auth.rejects_bad_key")}
    assert all(verdict == "pass" for key, verdict in v.items() if key not in skipped)
    assert TOKEN not in (run_dir / "exchanges.jsonl").read_text()
    assert located(run_dir) == set()


def test_layers_run_outermost_first_and_record_who_answered(stack_env, tmp_path):
    stack_env()
    _, run_dir = run(tmp_path, "-k", "models")
    targets = [json.loads(line)["target"] for line in (run_dir / "results.jsonl").open()]
    assert targets == list(LAYERS)
    served = {
        json.loads(line)["url"].rsplit("/v1", 1)[0]: json.loads(line)["served_by"]
        for line in (run_dir / "exchanges.jsonl").open()
    }
    # The router names the engine it picked; the gateway passes that through; the engine is
    # itself.
    assert sorted(served.values()) == ["engine", "engine-0", "engine-0"]
    stack = json.loads((run_dir / "layers.json").read_text())["stack"]
    assert stack == [
        {"name": "gateway", "kind": "proxy", "behind": "router"},
        {"name": "router", "kind": "router", "behind": "engine"},
        {"name": "engine", "kind": "engine", "behind": None},
    ]


@pytest.mark.parametrize(
    "defect, layer, case",
    [
        ("gateway_drops_done", "gateway", "stream.well_formed"),
        ("gateway_error_status", "gateway", "errors.unknown_model"),
        ("gateway_any_key", "gateway", "auth.rejects_bad_key"),
        ("router_drops_usage", "router", "chat.usage_adds_up"),
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
    for inner in BEHIND[layer]:
        # The layers behind are healthy: they pass (or skip, for auth) the same case.
        assert v.get((inner, case), "skip") in ("pass", "skip"), f"{defect} leaked into {inner}"
    # The defect is placed at its own layer, even when the layers in front of it fail too. It is
    # confirmed there when the layer behind it ran the case and passed; where that layer skipped
    # (auth, behind the gateway), the run says "this layer, or behind it".
    confirmed = not BEHIND[layer] or v[(BEHIND[layer][0], case)] == "pass"
    assert (case, layer, confirmed) in located(run_dir)
    assert {where for _, where, _ in located(run_dir)} == {layer}


def test_a_defect_fails_every_layer_in_front_of_it(stack_env, tmp_path):
    stack_env("router_drops_usage")
    _, run_dir = run(tmp_path, "-k", "chat.usage_adds_up")
    assert verdicts(run_dir) == {
        ("gateway", "chat.usage_adds_up"): "fail",
        ("router", "chat.usage_adds_up"): "fail",
        ("engine", "chat.usage_adds_up"): "pass",
    }


def test_latency_is_judged_against_each_layers_own_budget(stack_env, tmp_path):
    """The engine takes 1s: over its 800ms budget, within the router's 1200ms and the gateway's
    1500ms."""
    stack_env("engine_slow")
    code, run_dir = run(tmp_path, "-k", "chat.latency")
    v = verdicts(run_dir)
    assert code == ExitCode.FAILURES
    assert v == {
        ("gateway", "chat.latency"): "pass",
        ("router", "chat.latency"): "pass",
        ("engine", "chat.latency"): "fail",
    }


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
    assert code == ExitCode.OK and len(verdicts(run_dir)) == 24


def test_preflight_names_an_unreachable_layer(monkeypatch, tmp_path):
    for name in ("GATEWAY_URL", "ROUTER_URL", "ENGINE_URL"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("GATEWAY_TOKEN", TOKEN)
    code, run_dir = run(tmp_path)
    assert code == ExitCode.PREFLIGHT
    problems = json.loads((run_dir / "outcome.json").read_text())["preflight"]
    assert {p["kind"] for p in problems} == {"reachability"}


def test_a_broken_profile_reports_every_problem_and_exits_2(tmp_path):
    from va import ProfileError

    from modelcheck.profile import load

    broken = tmp_path / "broken.yaml"
    broken.write_text(
        "name: x\nmodle: m\ntargets:\n  - name: g\n    kind: proxy\n    base_url_env: G_URL\n"
        "    capabilites: [chat]\n    capabilities: [chats]\n"
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


@pytest.mark.parametrize(
    "line, problem",
    [
        # The YAML pitfalls in docs/profiles.md: each is caught with its path.
        ("model: 1.10", "model: must be str, not float"),
        ("model: no", "model: must be str, not bool"),
        ("model: 2024-05-01", "model: must be str, not date"),
    ],
)
def test_yaml_implicit_types_are_reported(tmp_path, line, problem):
    from va import ProfileError

    from modelcheck.profile import load

    path = tmp_path / "typed.yaml"
    path.write_text(
        f"name: x\n{line}\ntargets:\n  - name: g\n    kind: engine\n    base_url_env: G_URL\n"
        "    capabilities: [chat]\n"
    )
    with pytest.raises(ProfileError) as exc:
        load(path)
    assert problem in exc.value.problems


def test_the_same_profile_loads_from_toml_and_json(tmp_path):
    """YAML is the default; TOML and JSON describe the same profile, with the same digest."""
    import json

    import yaml

    from modelcheck.profile import load

    data = yaml.safe_load(PROFILE.read_text())
    as_json = tmp_path / "stack.json"
    as_json.write_text(json.dumps(data))
    as_toml = tmp_path / "stack.toml"
    as_toml.write_text(
        'name = "demo-stack"\nmodel = "demo-model"\nmax_latency_ms = 800\n'
        'description = "a gateway, the router behind it, and its engine"\n'
        '[[targets]]\nname = "gateway"\nkind = "proxy"\nbehind = "router"\n'
        'base_url_env = "GATEWAY_URL"\nkey_env = "GATEWAY_TOKEN"\n'
        'capabilities = ["models", "chat", "stream", "auth"]\nmax_latency_ms = 1500\n'
        "[targets.expect]\nunknown_model_status = 404\nbad_key_status = 401\n"
        '[[targets]]\nname = "router"\nkind = "router"\nbehind = "engine"\n'
        'base_url_env = "ROUTER_URL"\ncapabilities = ["models", "chat", "stream"]\n'
        "max_latency_ms = 1200\n[targets.expect]\nunknown_model_status = 404\n"
        '[[targets]]\nname = "engine"\nkind = "engine"\nbase_url_env = "ENGINE_URL"\n'
        'capabilities = ["models", "chat", "stream"]\n'
        "[targets.expect]\nunknown_model_status = 404\n"
    )
    digests = {load(p).digest for p in (PROFILE, as_json, as_toml)}
    assert len(digests) == 1


@pytest.mark.parametrize(
    "targets, problem",
    [
        (
            "  - {name: g, kind: proxy, behind: nowhere, base_url_env: G_URL}\n",
            "targets[0].behind: names no target: 'nowhere'",
        ),
        (
            "  - {name: g, kind: gateway, base_url_env: G_URL}\n",
            "targets[0].kind: must be one of proxy, router, engine, not 'gateway'",
        ),
        (
            "  - {name: e, kind: engine, behind: g, base_url_env: E_URL}\n"
            "  - {name: g, kind: proxy, base_url_env: G_URL}\n",
            "targets[0].behind: e (engine) cannot be in front of g (proxy): proxy is an outer kind",
        ),
        (
            "  - {name: a, kind: router, behind: b, base_url_env: A_URL}\n"
            "  - {name: b, kind: router, behind: a, base_url_env: B_URL}\n",
            "targets: the layers behind a form a cycle",
        ),
    ],
)
def test_a_stack_that_cannot_exist_is_refused(tmp_path, targets, problem):
    from va import ProfileError

    from modelcheck.profile import load

    path = tmp_path / "stack.yaml"
    path.write_text(f"name: x\nmodel: m\ntargets:\n{targets}")
    with pytest.raises(ProfileError) as exc:
        load(path)
    assert problem in exc.value.problems


def test_layers_are_ordered_whatever_order_the_file_lists_them(tmp_path):
    from modelcheck.profile import load

    path = tmp_path / "stack.yaml"
    path.write_text(
        "name: x\nmodel: m\ntargets:\n"
        "  - {name: e, kind: engine, base_url_env: E_URL}\n"
        "  - {name: g, kind: proxy, behind: e, base_url_env: G_URL}\n"
    )
    assert [t.name for t in load(path).targets] == ["g", "e"]
