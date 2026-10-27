"""Tests for va.layers: layer kinds, stack validation, ordering and failure location."""

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
    """Minimal layer description used by the tests.

    Attributes:
        name: Layer name.
        kind: Registered layer kind.
        behind: Name of the layer directly behind this one, if any.
    """

    name: str
    kind: str
    behind: str | None = None


def kinds() -> LayerKinds:
    """Return layer kinds for a proxy, a router and an engine, outermost first."""
    k = LayerKinds()
    k.register("proxy", depth=0)
    k.register("router", depth=1, adapter=HeaderServedBy("X-Served-By"))
    k.register("engine", depth=2, adapter=AnswersItself())
    return k


STACK = [Layer("gw", "proxy", "rt"), Layer("rt", "router", "en"), Layer("en", "engine")]


def test_kinds_are_listed_outermost_first_and_registered_once():
    """Verify that kinds are listed by depth and cannot be registered twice."""
    k = LayerKinds()
    k.register("engine", depth=2)
    k.register("proxy", depth=0)
    assert k.names == ("proxy", "engine")
    with pytest.raises(ValueError, match="already registered"):
        k.register("proxy", depth=5)


def test_order_is_by_depth_and_stable_within_a_kind():
    """Verify that layers are ordered by depth and keep their order within a kind."""
    k = kinds()
    shuffled = [
        Layer("en", "engine"),
        Layer("b", "proxy"),
        Layer("rt", "router"),
        Layer("a", "proxy"),
    ]
    assert [x.name for x in k.order(shuffled)] == ["b", "a", "rt", "en"]


def test_chain_follows_behind_inwards_and_survives_a_cycle():
    """Verify that a chain follows the behind links inwards and stops at a cycle."""
    assert [x.name for x in LayerKinds.chain(STACK, "gw")] == ["gw", "rt", "en"]
    assert [x.name for x in LayerKinds.chain(STACK, "rt")] == ["rt", "en"]
    loop = [Layer("a", "router", "b"), Layer("b", "router", "a")]
    assert [x.name for x in LayerKinds.chain(loop, "a")] == ["a", "b"]


def test_parse_collects_problems_instead_of_raising():
    """Verify that parsing records problems instead of raising."""
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
    """Verify that an impossible stack is reported with the offending path."""
    p = Problems()
    kinds().check_stack(p, stack)
    assert problem in p.items


def test_a_valid_stack_has_no_problems():
    """Verify that a valid stack produces no problems."""
    p = Problems()
    kinds().check_stack(p, STACK + [Layer("p2", "proxy", "p3"), Layer("p3", "proxy")])
    assert p.items == []


def test_adapters():
    """Verify how each adapter reports the layer that served a request."""
    k = kinds()
    router, engine, proxy = STACK[1], STACK[2], STACK[0]
    assert k.adapter(router).served_by(router, {"x-served-by": "engine-3"}) == "engine-3"
    assert k.adapter(router).served_by(router, {}) is None
    assert k.adapter(engine).served_by(engine, {}) == "en"
    # The default adapter does not identify a layer.
    assert k.adapter(proxy).served_by(proxy, {"x-served-by": "x"}) is None
    assert LayerAdapter().request_headers(proxy) == {}


P, F, E, S = Verdict.PASS, Verdict.FAIL, Verdict.ERROR, Verdict.SKIP


@pytest.mark.parametrize(
    "verdicts, expected",
    [
        ({"gw": P, "rt": P, "en": P}, []),
        ({"gw": F, "rt": P, "en": P}, [Location("gw", True)]),
        # The gateway repeats the router failure.
        ({"gw": F, "rt": F, "en": P}, [Location("rt", True)]),
        ({"gw": F, "rt": E, "en": F}, [Location("en", True)]),  # An error counts as a failure.
        ({"gw": F, "rt": S, "en": P}, [Location("gw", False)]),  # The router skipped the case.
        ({"gw": F, "en": P}, [Location("gw", False)]),  # The router is not part of this run.
        # The router is blamed although the outer layer passed.
        ({"gw": P, "rt": F, "en": P}, [Location("rt", True)]),
        # Verdicts given as plain strings.
        ({"gw": "fail", "rt": "pass", "en": "pass"}, [Location("gw", True)]),
    ],
)
def test_locate(verdicts, expected):
    """Verify that a failure is located at the innermost failing layer."""
    assert LayerKinds.locate(STACK, verdicts) == expected


def test_the_locator_hook_writes_layers_json(tmp_path):
    """Verify that the layer locator hook writes failure locations to layers.json."""
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
