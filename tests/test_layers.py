"""va.layers: kinds, stack validation, ordering, and where a failure starts."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from va import (
    AnswersItself,
    HeaderServedBy,
    LayerAdapter,
    LayerKinds,
    LayerLocator,
    Location,
    Problems,
    ResultRecord,
    RunDir,
    RunOutcome,
    Verdict,
)


@dataclass(frozen=True)
class Layer:
    name: str
    kind: str
    behind: str | None = None


def kinds() -> LayerKinds:
    k = LayerKinds()
    k.register("proxy", depth=0)
    k.register("router", depth=1, adapter=HeaderServedBy("X-Served-By"))
    k.register("engine", depth=2, adapter=AnswersItself())
    return k


STACK = [Layer("gw", "proxy", "rt"), Layer("rt", "router", "en"), Layer("en", "engine")]


def test_kinds_are_listed_outermost_first_and_registered_once():
    k = LayerKinds()
    k.register("engine", depth=2)
    k.register("proxy", depth=0)
    assert k.names == ("proxy", "engine")
    with pytest.raises(ValueError, match="already registered"):
        k.register("proxy", depth=5)


def test_order_is_by_depth_and_stable_within_a_kind():
    k = kinds()
    shuffled = [
        Layer("en", "engine"),
        Layer("b", "proxy"),
        Layer("rt", "router"),
        Layer("a", "proxy"),
    ]
    assert [x.name for x in k.order(shuffled)] == ["b", "a", "rt", "en"]


def test_chain_follows_behind_inwards_and_survives_a_cycle():
    assert [x.name for x in LayerKinds.chain(STACK, "gw")] == ["gw", "rt", "en"]
    assert [x.name for x in LayerKinds.chain(STACK, "rt")] == ["rt", "en"]
    loop = [Layer("a", "router", "b"), Layer("b", "router", "a")]
    assert [x.name for x in LayerKinds.chain(loop, "a")] == ["a", "b"]


def test_parse_collects_problems_instead_of_raising():
    p = Problems()
    assert kinds().parse(p, {"kind": "gateway"}, "targets[0]") == ("", None)
    assert kinds().parse(p, {}, "targets[1]") == ("", None)
    assert p.items == [
        "targets[0].kind: must be one of proxy, router, engine, not 'gateway'",
        "targets[1].kind: is required",
    ]


@pytest.mark.parametrize(
    "stack, problem",
    [
        ([Layer("a", "proxy", "zz")], "targets[0].behind: names no target: 'zz'"),
        ([Layer("a", "proxy", "a")], "targets[0].behind: a layer cannot be behind itself"),
        (
            [Layer("e", "engine", "p"), Layer("p", "proxy")],
            "targets[0].behind: e (engine) cannot be in front of p (proxy): proxy is an outer kind",
        ),
        (
            [Layer("a", "router", "b"), Layer("b", "router", "a")],
            "targets: the layers behind a form a cycle",
        ),
    ],
)
def test_a_stack_that_cannot_exist_is_reported(stack, problem):
    p = Problems()
    kinds().check_stack(p, stack)
    assert problem in p.items


def test_a_valid_stack_has_no_problems():
    p = Problems()
    kinds().check_stack(p, STACK + [Layer("p2", "proxy", "p3"), Layer("p3", "proxy")])
    assert p.items == []


def test_adapters():
    k = kinds()
    router, engine, proxy = STACK[1], STACK[2], STACK[0]
    assert k.adapter(router).served_by(router, {"x-served-by": "engine-3"}) == "engine-3"
    assert k.adapter(router).served_by(router, {}) is None
    assert k.adapter(engine).served_by(engine, {}) == "en"
    assert (
        k.adapter(proxy).served_by(proxy, {"x-served-by": "x"}) is None
    )  # the default says nothing
    assert LayerAdapter().request_headers(proxy) == {}


P, F, E, S = Verdict.PASS, Verdict.FAIL, Verdict.ERROR, Verdict.SKIP


@pytest.mark.parametrize(
    "verdicts, expected",
    [
        ({"gw": P, "rt": P, "en": P}, []),
        ({"gw": F, "rt": P, "en": P}, [Location("gw", True)]),
        ({"gw": F, "rt": F, "en": P}, [Location("rt", True)]),  # the gateway only repeats it
        ({"gw": F, "rt": E, "en": F}, [Location("en", True)]),  # an error counts as a failure
        ({"gw": F, "rt": S, "en": P}, [Location("gw", False)]),  # the router did not run it
        ({"gw": F, "en": P}, [Location("gw", False)]),  # the router is not in this run
        ({"gw": P, "rt": F, "en": P}, [Location("rt", True)]),  # outer passes: still the router
        ({"gw": "fail", "rt": "pass", "en": "pass"}, [Location("gw", True)]),  # plain strings
    ],
)
def test_locate(verdicts, expected):
    assert LayerKinds.locate(STACK, verdicts) == expected


def test_the_locator_hook_writes_layers_json(tmp_path):
    run = RunDir.create(tmp_path)
    hook = LayerLocator(STACK)
    for target, verdict in (("gw", F), ("rt", F), ("en", P)):
        hook.on_result(
            run,
            ResultRecord(run_id=run.run_id, case_id="chat.usage", target=target, verdict=verdict),
        )
    hook.on_result(run, ResultRecord(run_id=run.run_id, case_id="models", target="gw", verdict=P))
    hook.on_finish(run, RunOutcome(run_id=run.run_id, tool="t"))
    data = json.loads(run.file("layers.json").read_text())
    assert data["schema"] == "va.layers/1"
    assert [layer["name"] for layer in data["stack"]] == ["gw", "rt", "en"]
    assert data["failures"] == [
        {
            "case_id": "chat.usage",
            "variant": "",
            "layer": "rt",
            "confirmed": True,
            "verdicts": {"gw": "fail", "rt": "fail", "en": "pass"},
        }
    ]
