"""va's own tests: no network beyond localhost, no cluster."""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass

import pytest

from va import kube, retry
from va.cli import App, UsageError
from va.compare import ChangeKind, compare
from va.defaults import RetryPolicy
from va.exit_codes import ExitCode, combine, label_of
from va.hooks import RunHooks
from va.layout import RunFile
from va.log import configure, emit, get_logger
from va.model import (
    Check,
    ResultRecord,
    RunOutcome,
    SkipClass,
    SkipClasses,
    Verdict,
)
from va.plugins import PLUGINS, PluginRegistry, UnknownPlugin
from va.profile import Problems, ProfileError, load
from va.publish import Archive, deliver
from va.redact import REDACTOR, Redactor
from va.rundir import RunDir
from va.runner import CaseRegistry, Runner, Selector
from va.settings import Key, Settings
from va.sinks import FileSink, UnsupportedDestination, sink_for


@pytest.fixture(autouse=True)
def _clean():
    yield
    REDACTOR.clear()


# ------------------------------------------------------------------ exit codes


def test_delivery_never_masks_a_verdict():
    assert combine(ExitCode.OK, False) is ExitCode.NOT_DELIVERED
    assert combine(ExitCode.FAILURES, False) is ExitCode.FAILURES
    assert combine(ExitCode.PREFLIGHT, False) is ExitCode.PREFLIGHT
    assert combine(ExitCode.OK, True) is ExitCode.OK
    assert label_of(None) == "crashed" and label_of(-9) == "crashed"
    assert ExitCode.parse(3) is ExitCode.PREFLIGHT and ExitCode.parse(137) is None


# ------------------------------------------------------------------ redaction and logging


def test_secret_is_scrubbed_from_every_stream(tmp_path):
    REDACTOR.add("s3cr3t-value-xyz")
    err, out = io.StringIO(), io.StringIO()
    setup = configure(app="myapp", level="DEBUG", stderr=err, stdout=out)
    log_file = tmp_path / "debug.log"
    handler = setup.add_file(log_file)
    # Child loggers of both trees: a filter on a logger (not a handler) would miss these.
    get_logger("myapp.deep.child").debug("token=%s", "s3cr3t-value-xyz")
    get_logger("publish").warning("Authorization: Bearer abcdefghijklmnop")
    try:
        raise RuntimeError("boom s3cr3t-value-xyz")
    except RuntimeError:
        get_logger("myapp.cli").exception("failed")
    emit("primary s3cr3t-value-xyz")
    setup.remove(handler)
    text = err.getvalue() + out.getvalue() + log_file.read_text()
    assert "s3cr3t-value-xyz" not in text and "abcdefghijklmnop" not in text
    assert text.count("***REDACTED***") >= 5


def test_primary_output_survives_quiet_logging():
    err, out = io.StringIO(), io.StringIO()
    configure(app="myapp", level="ERROR", stderr=err, stdout=out)
    get_logger("myapp.x").info("diagnostic")
    emit("the report")
    assert out.getvalue() == "the report\n" and "diagnostic" not in err.getvalue()


def test_redactor_is_injectable_and_reads_secrets_from_env():
    mine = Redactor()
    assert mine.secret_from_env("T", environ={"T": "abc123456789"}) == "abc123456789"
    assert mine.scrub("x abc123456789") == "x ***REDACTED***"
    assert REDACTOR.scrub("x abc123456789") == "x abc123456789"  # the process one is untouched
    with pytest.raises(KeyError):
        mine.secret_from_env("UNSET", environ={})


# ------------------------------------------------------------------ typed data round-trips


def test_records_and_outcomes_round_trip_through_json(tmp_path):
    run = RunDir.create(tmp_path)
    record = ResultRecord(
        run.run_id,
        "a.b",
        Verdict.FAIL,
        target="t",
        variant="v",
        checks=[Check("status", 500, 200, False)],
        reason="status",
    )
    run.add_result(record)
    assert run.results() == [record]
    outcome = RunOutcome(run_id=run.run_id, tool="t")
    outcome.record([record])
    outcome.finish(ExitCode.FAILURES)
    run.save_outcome(outcome)
    back = run.outcome()
    assert back == outcome and back.exit is ExitCode.FAILURES
    assert back.failures[0].key.case_id == "a.b" and back.failures[0].check == "status"


def test_a_skip_needs_a_class_and_only_a_skip_has_one():
    with pytest.raises(ValueError):
        ResultRecord("r", "c", Verdict.SKIP)
    with pytest.raises(ValueError):
        ResultRecord("r", "c", Verdict.PASS, skip_class="undeclared")


def test_skip_classes_are_immutable_and_extended_by_copy():
    extended = SkipClasses.default().extend(SkipClass("requires_load", "needs a load generator"))
    assert "requires_load" in extended and "requires_load" not in SkipClasses.default()
    with pytest.raises(ValueError):
        extended.extend(SkipClass("requires_load", "something else"))


# ------------------------------------------------------------------ run directory


def test_manifest_is_last_and_detects_tampering(tmp_path):
    REDACTOR.add("hunter2-hunter2")
    run = RunDir.create(tmp_path)
    run.write_json("outcome.json", {"note": "key hunter2-hunter2"})
    run.write_text("evidence/a/b.txt", "x")
    order = run.write_manifest()
    assert order[-1] == RunFile.MANIFEST and "evidence/a/b.txt" in order
    assert "hunter2" not in run.file("outcome.json").read_text()
    assert run.verify_manifest() == []
    run.write_text("evidence/a/b.txt", "changed")
    run.write_text("late.txt", "added after")
    problems = run.verify_manifest()
    assert any("hash differs" in p for p in problems) and any("not listed" in p for p in problems)


# ------------------------------------------------------------------ runner


@dataclass(frozen=True)
class T:
    name: str
    capabilities: frozenset[str] = frozenset()


def test_runner_verdicts_skips_hooks_and_no_retry(tmp_path, caplog):
    reg = CaseRegistry()
    calls = {"flaky": 0}
    seen: list[str] = []

    class Seen(RunHooks):
        def on_result(self, run, record):
            seen.append(record.case_id)

    @reg.case("a.pass", tags={"smoke"})
    def _p(ctx):
        ctx.require("one", 1, 1)

    @reg.case("a.fail")
    def _f(ctx):
        ctx.require("status", 500, 200)

    @reg.case("a.error")
    def _e(ctx):
        raise ValueError("cannot decide")

    @reg.case("a.needs", needs={"gpu"})
    def _n(ctx):
        ctx.require("never", 0, 1)

    @reg.case("a.flaky")
    def _fl(ctx):
        calls["flaky"] += 1
        ctx.require("first_try", calls["flaky"], 2)

    @reg.case("a.empty")
    def _empty(ctx):
        pass

    @reg.case("a.badskip")
    def _bad(ctx):
        ctx.skip("no_such_class", "why")

    @reg.case("a.variants", variants=("x", "y"))
    def _v(ctx):
        ctx.require("variant", ctx.variant, "x")

    run = RunDir.create(tmp_path)
    with caplog.at_level(logging.WARNING):
        records = Runner(run, hooks=Seen()).run_all(reg.select(), [T("t1")])
    got = {(r.case_id, r.variant): r for r in records}
    assert got[("a.pass", "")].verdict is Verdict.PASS
    assert got[("a.fail", "")].failed_check.observed == 500
    assert got[("a.error", "")].verdict is Verdict.ERROR
    assert (run.path / "evidence/t1/a.error/traceback.txt").is_file()
    assert got[("a.needs", "")].skip_class == "undeclared"
    assert got[("a.flaky", "")].verdict is Verdict.FAIL and calls["flaky"] == 1  # never retried
    assert got[("a.badskip", "")].verdict is Verdict.ERROR  # an unknown skip class is a defect
    assert got[("a.variants", "y")].verdict is Verdict.FAIL
    assert "proved nothing" in caplog.text
    assert seen == [r.case_id for r in records] and run.results() == records


def test_selector_by_pattern_and_tags():
    reg = CaseRegistry()
    for cid, tags in (("api.a", {"smoke"}), ("api.b", {"slow"}), ("health.a", {"smoke", "slow"})):
        reg.case(cid, tags=tags)(lambda ctx: None)
    ids = lambda sel: [c.case_id for c in reg.select(sel)]  # noqa: E731
    assert ids(Selector(patterns=("api",))) == ["api.a", "api.b"]
    assert ids(Selector(tags=frozenset({"smoke"}))) == ["api.a", "health.a"]
    assert ids(Selector(tags=frozenset({"smoke"}), exclude_tags=frozenset({"slow"}))) == ["api.a"]


# ------------------------------------------------------------------ sinks, publish, archive


def test_publish_manifest_last_and_partial_runs_are_not_baselines(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    run = RunDir.create(tmp_path / "w")
    run.add_result(ResultRecord(run.run_id, "c", Verdict.PASS))
    order: list[str] = []
    original = FileSink.put
    monkeypatch.setattr(
        FileSink, "put", lambda self, local, key: (order.append(key), original(self, local, key))
    )
    publication = deliver(run, f"file://{archive}", "grp")
    assert publication.delivered and order[-1].endswith(RunFile.MANIFEST)
    partial = archive / "grp" / "2099-partial"
    partial.mkdir(parents=True)
    (partial / "results.jsonl").write_text("{}\n")
    assert Archive.at(f"file://{archive}").runs("grp") == [run.run_id]


def test_unknown_scheme_and_failures_are_recorded_not_raised(tmp_path):
    with pytest.raises(UnsupportedDestination, match="known: file, s3"):
        sink_for("ftp://nowhere")
    publication = deliver(RunDir.create(tmp_path), "ftp://nowhere", "g")
    assert publication.status == "failed" and not publication.delivered


def test_s3_without_credentials_is_skipped(tmp_path, monkeypatch):
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_PROFILE",
        "AWS_ROLE_ARN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    publication = deliver(RunDir.create(tmp_path), "s3://bucket/prefix", "g")
    assert publication.status == "skipped"


def test_a_sink_plugin_is_one_registration(tmp_path):
    stored: dict[str, bytes] = {}

    class MemorySink:
        def __init__(self, uri):
            self.uri = uri

        def unavailable_reason(self):
            return None

        def write_check(self):
            pass

        def put(self, local, key):
            stored[key] = local.read_bytes()

        def get(self, key):
            return stored[key]

        def list(self, prefix):
            return sorted(k for k in stored if k.startswith(prefix + "/"))

        def location(self, key):
            return f"{self.uri}/{key}"

    PLUGINS.sinks.register("mem", MemorySink, replace=True)
    run = RunDir.create(tmp_path)
    run.add_result(ResultRecord(run.run_id, "c", Verdict.PASS))
    assert deliver(run, "mem://x", "g").delivered
    assert Archive.at("mem://x").runs("g") == [run.run_id]


def test_plugin_registry_rejects_shadowing():
    registry: PluginRegistry[int] = PluginRegistry("thing")
    registry.register("a", 1)
    with pytest.raises(ValueError):
        registry.register("A", 2)
    with pytest.raises(UnknownPlugin, match="known: a"):
        registry.get("b")


# ------------------------------------------------------------------ compare


def _r(case, verdict, skip_class=None):
    return ResultRecord("r", case, Verdict(verdict), target="t", skip_class=skip_class)


def test_four_transitions_and_flaky_window():
    base1 = [
        _r("reg", "pass"),
        _r("rec", "fail"),
        _r("lost", "pass"),
        _r("new", "skip", "x"),
        _r("flk", "fail"),
    ]
    base2 = [
        _r("reg", "pass"),
        _r("rec", "fail"),
        _r("lost", "pass"),
        _r("new", "skip", "x"),
        _r("flk", "pass"),
    ]
    now = [
        _r("reg", "fail"),
        _r("rec", "pass"),
        _r("lost", "skip", "throttled"),
        _r("new", "fail"),
        _r("flk", "fail"),
        _r("brand", "pass"),
    ]
    result = compare(now, [("r1", base1), ("r2", base2)], "r3")
    kinds = {c.key.case_id: c.kind for c in result.changes}
    assert kinds == {
        "reg": ChangeKind.REGRESSION,
        "rec": ChangeKind.RECOVERY,
        "lost": ChangeKind.LOST_EVIDENCE,
        "new": ChangeKind.NEW_EVIDENCE,
        "flk": ChangeKind.FLAKY,
        "brand": ChangeKind.NEW_CASE,
    }
    assert "Regressions (1)" in result.markdown()
    assert type(result).from_dict(result.to_dict()).changes == result.changes


def test_zero_cases_is_never_a_pass():
    result = compare([], [("r1", [_r("a", "pass")])])
    assert result.problems and not result.regressed


# ------------------------------------------------------------------ kube, retry, profile, settings


@pytest.mark.parametrize("verb", ["apply", "delete", "scale", "exec", "patch", "create"])
def test_kube_refuses_writes(verb):
    with pytest.raises(kube.KubeError, match="read-only"):
        kube.KubeContext(cli="kubectl")._run(verb, "pods")
    with pytest.raises(kube.KubeError, match="can-i"):
        kube.KubeContext()._run("auth", "reconcile")


def test_retry_is_bounded_and_caps_retry_after():
    attempts = []

    def failing():
        attempts.append(1)
        raise OSError("down")

    policy = RetryPolicy(attempts=3)
    with pytest.raises(OSError):
        retry.call(failing, what="x", policy=policy, sleep=lambda s: None)
    assert len(attempts) == 3

    def long_wait():
        raise retry.RetryAfter(3600)

    with pytest.raises(retry.RetryAfter):
        retry.call(long_wait, what="x", sleep=lambda s: None)


def test_problems_reports_every_path():
    p = Problems()
    p.require({}, "name", str)
    p.env_name("not-a-var", "targets[0].key_env")
    p.check_type(True, int, "count")
    with pytest.raises(ProfileError) as exc:
        p.raise_if_any("profile.toml")
    assert len(exc.value.problems) == 3


def test_profile_format_is_a_plugin(tmp_path):
    PLUGINS.profile_loaders.register(
        "ini", lambda text: dict(line.split("=", 1) for line in text.splitlines()), replace=True
    )
    path = tmp_path / "p.ini"
    path.write_text("name=x")
    assert load(path) == {"name": "x"}
    with pytest.raises(ProfileError, match="unknown profile format"):
        load(tmp_path / "p.unknown")


def test_settings_prefix_and_types():
    s = Settings.for_app(
        "my-tool", environ={"MY_TOOL_RUN_BUDGET": "60", "MY_TOOL_NOTIFY": "a:, b:x"}
    )
    assert s.name(Key.RESULTS_URI) == "MY_TOOL_RESULTS_URI"
    assert s.get_float(Key.RUN_BUDGET) == 60.0 and s.get_list(Key.NOTIFY) == ["a:", "b:x"]
    with pytest.raises(ValueError):
        Settings("X", {"X_RUN_BUDGET": "soon"}).get_float(Key.RUN_BUDGET)


# ------------------------------------------------------------------ cli


class _Cmd:
    name, help, passthrough = "boom", "", False

    def __init__(self, exc):
        self.exc = exc

    def add_arguments(self, parser, app):
        pass

    def run(self, args, app):
        raise self.exc


@pytest.mark.parametrize(
    "exc, code",
    [
        (RuntimeError("bug"), 4),
        (ValueError("bug"), 4),
        (UsageError("bad"), 2),
        (ProfileError("p", ["x"]), 2),
        (KeyboardInterrupt(), 6),
    ],
)
def test_cli_maps_exceptions_to_blame(exc, code):
    assert App("t", version="0").add(_Cmd(exc)).main(["boom"]) == code


def test_cli_rejects_unknown_arguments_unless_passthrough():
    with pytest.raises(SystemExit) as exc:
        App("t", version="0").add(_Cmd(RuntimeError())).main(["boom", "--nope"])
    assert exc.value.code == 2


def test_child_argv_prefers_the_module():
    import sys

    assert App("t", version="0", module="pkg").child_argv("run") == [
        sys.executable,
        "-m",
        "pkg",
        "run",
    ]


def test_third_party_plugins_load_from_entry_points(monkeypatch):
    class Point:
        name = "teams"

        @staticmethod
        def load():
            return "the-teams-factory"

    import va.plugins as plugins

    monkeypatch.setattr(plugins, "entry_points", lambda group: [Point()] if group == "g" else [])
    registry: PluginRegistry[str] = PluginRegistry("notifier", "g")
    assert registry.get("teams") == "the-teams-factory" and registry.names() == ["teams"]


def test_notify_flag_replaces_the_environment(tmp_path):
    from va.commands import notify_specs

    app = App("t", version="0", settings=Settings("T", {"T_NOTIFY": "log:,file:/x"}))
    parser = app.add_builtins().build_parser()
    from_env = parser.parse_args(["notify", str(tmp_path)])
    from_flag = parser.parse_args(["notify", str(tmp_path), "--notify", "log:"])
    assert notify_specs(from_env, app) == ["log:", "file:/x"]
    assert notify_specs(from_flag, app) == ["log:"]


def test_compare_command_has_no_compare_switch():
    parser = App("t", version="0").add_builtins().build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["compare", "run", "--compare"])


def test_problems_catch_misspelled_keys_and_ranges():
    p = Problems()
    p.unknown_keys({"name": 1, "capabilites": []}, {"name", "capabilities"}, at="targets[0]")
    p.int_range(700, 100, 599, "targets[0].errors.unknown_model")
    p.int_range(404, 100, 599, "ok")
    assert p.items == [
        "targets[0].capabilites: is not a known key; did you mean 'capabilities'?",
        "targets[0].errors.unknown_model: must be between 100 and 599, not 700",
    ]


def test_run_ids_sort_in_start_order_within_a_second():
    from va.rundir import new_run_id

    ids = []
    for _ in range(20):
        ids.append(new_run_id())
        time.sleep(0.002)
    assert ids == sorted(ids)
