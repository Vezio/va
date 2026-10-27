"""Layers: targets that are the ingresses of one stack, each in front of the next.

A serving stack is reached at several points: a gateway, a router behind it, an engine behind
that. Declared as layers, each target carries a **kind** and, optionally, the name of the target
it is **behind** (forwards to). The framework then:

- validates the declaration: every kind known, every `behind` naming another target, no cycle,
  no layer in front of a layer of an outer kind;
- orders the targets outermost first, so every column, report and listing reads from the
  client's side inwards;
- gives each kind an **adapter**: the one place for what differs about calling a kind of layer
  (headers it needs, how it says which backend answered). Cases stay generic;
- **locates** a failure: when the same case fails at a layer and passes at the one behind it,
  the defect is in that layer, not deeper. `LayerLocator`, a run hook, does it for every
  failing case and writes `layers.json`.

Kinds belong to the tool, not the framework: a tool makes one `LayerKinds`, registers its kinds
with their depth, and parses profiles with it. Nothing here is module state.

    KINDS = LayerKinds()
    KINDS.register("proxy", depth=0, description="an API gateway or proxy")
    KINDS.register("router", depth=1, adapter=RouterAdapter())
    KINDS.register("engine", depth=2)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from .hooks import RunHooks
from .layout import RunFile, Schema
from .log import get_logger
from .model import ResultRecord, RunOutcome, Verdict
from .profile import Problems
from .rundir import RunDir

log = get_logger("layers")


class Layered(Protocol):
    """A target declared as a layer. `behind` is the name of the target this one forwards to,
    or None for the innermost layer. `locate` reads None as innermost, so declare `behind` on
    every layer in front of another."""

    @property
    def name(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def behind(self) -> str | None: ...


L = TypeVar("L", bound=Layered)


class LayerAdapter:
    """What differs about calling one kind of layer. Subclass and override only what differs;
    the defaults suit a layer that needs nothing and does not say who answered."""

    def request_headers(self, layer: Layered) -> dict[str, str]:
        """Headers every request to this layer carries (to route to a backend, say)."""
        return {}

    def served_by(self, layer: Layered, headers: Mapping[str, str]) -> str | None:
        """Which component answered, from the response headers; None when the layer does not
        say. Recorded as evidence with each exchange; never a verdict on its own."""
        return None


class HeaderServedBy(LayerAdapter):
    """A layer that names the component that answered in a response header."""

    def __init__(self, header: str):
        self.header = header.lower()

    def served_by(self, layer: Layered, headers: Mapping[str, str]) -> str | None:
        for key, value in headers.items():
            if key.lower() == self.header:
                return value
        return None


class AnswersItself(LayerAdapter):
    """The innermost layer: whatever answers is the layer itself."""

    def served_by(self, layer: Layered, headers: Mapping[str, str]) -> str | None:
        return layer.name


@dataclass(frozen=True)
class LayerKind:
    """A kind of layer. `depth` orders kinds from the client inwards: 0 is called first."""

    name: str
    depth: int
    description: str = ""
    adapter: LayerAdapter = field(default_factory=LayerAdapter)


@dataclass(frozen=True)
class Location:
    """Where a failure starts. `confirmed` is False when the layer behind it did not run the
    case (skipped, or not declared), so the defect could also be deeper."""

    layer: str
    confirmed: bool


class LayerKinds:
    """A tool's registry of layer kinds, and the operations over a declared stack."""

    def __init__(self) -> None:
        self._kinds: dict[str, LayerKind] = {}

    def register(
        self,
        name: str,
        depth: int,
        *,
        description: str = "",
        adapter: LayerAdapter | None = None,
    ) -> LayerKind:
        if name in self._kinds:
            raise ValueError(f"layer kind {name!r} is already registered")
        kind = LayerKind(name, depth, description, adapter or LayerAdapter())
        self._kinds[name] = kind
        return kind

    def __contains__(self, name: object) -> bool:
        return name in self._kinds

    def __getitem__(self, name: str) -> LayerKind:
        return self._kinds[name]

    @property
    def names(self) -> tuple[str, ...]:
        """Kind names, outermost first."""
        return tuple(k.name for k in sorted(self._kinds.values(), key=lambda k: k.depth))

    def adapter(self, layer: Layered) -> LayerAdapter:
        return self._kinds[layer.kind].adapter

    # -- profiles --------------------------------------------------------------------------

    def parse(self, p: Problems, raw: Mapping, at: str) -> tuple[str, str | None]:
        """Read `kind` (required, one of the registered kinds) and `behind` (optional) from one
        target's table. Problems are collected, not raised; check the whole stack with
        `check_stack` once every target is read."""
        kind = p.one_of(p.require(raw, "kind", str, at=at), self.names, f"{at}.kind")
        behind = p.optional(raw, "behind", str, at=at)
        return (kind if kind in self._kinds else ""), behind

    def check_stack(self, p: Problems, layers: Sequence[Layered], at: str = "targets") -> None:
        """Rules across targets: `behind` names another target, a layer is never in front of an
        outer kind, and following `behind` always ends."""
        by_name = {layer.name: layer for layer in layers}
        for i, layer in enumerate(layers):
            if layer.behind is None:
                continue
            path = f"{at}[{i}].behind"
            inner = by_name.get(layer.behind)
            if inner is None:
                p.add(path, f"names no target: {layer.behind!r}")
            elif inner is layer:
                p.add(path, "a layer cannot be behind itself")
            elif layer.kind in self._kinds and inner.kind in self._kinds:
                if self._kinds[inner.kind].depth < self._kinds[layer.kind].depth:
                    p.add(
                        path,
                        f"{layer.name} ({layer.kind}) cannot be in front of "
                        f"{inner.name} ({inner.kind}): {inner.kind} is an outer kind",
                    )
        for layer in layers:
            seen = {layer.name}
            current = by_name.get(layer.behind or "")
            while current is not None and current.behind is not None:
                if current.name in seen:
                    p.add(at, f"the layers behind {layer.name} form a cycle")
                    break
                seen.add(current.name)
                current = by_name.get(current.behind)

    # -- a declared stack ------------------------------------------------------------------

    def order(self, layers: Iterable[L]) -> list[L]:
        """Outermost first; layers of the same kind keep their declared order."""
        return sorted(layers, key=lambda layer: self._kinds[layer.kind].depth)

    @staticmethod
    def chain(layers: Sequence[L], name: str) -> list[L]:
        """The layer named, then every layer behind it, inwards."""
        by_name = {layer.name: layer for layer in layers}
        out: list[L] = []
        current = by_name.get(name)
        while current is not None and current not in out:
            out.append(current)
            current = by_name.get(current.behind or "")
        return out

    @staticmethod
    def locate(layers: Sequence[Layered], verdicts: Mapping[str, Verdict | str]) -> list[Location]:
        """Where one case's failures start, given its verdict per layer name. A failing layer
        whose `behind` passed is where the defect is; a failing layer behind which nothing ran
        is where it is at the latest. Failures that only repeat one from further in are not
        reported again."""
        failing = {Verdict.FAIL, Verdict.ERROR}

        def failed(name: str) -> bool:
            return Verdict(verdicts[name]) in failing

        out = []
        for layer in layers:
            if layer.name not in verdicts or not failed(layer.name):
                continue
            behind = layer.behind
            if behind is not None and behind in verdicts and failed(behind):
                continue  # the failure is repeated from further in
            passed_behind = (
                behind is not None
                and behind in verdicts
                and Verdict(verdicts[behind]) is Verdict.PASS
            )
            out.append(Location(layer.name, confirmed=behind is None or passed_behind))
        return out


class LayerLocator(RunHooks):
    """A run hook: once the run ends, where each failing case's failure starts. Writes
    `layers.json` (the stack as declared, and one entry per failing case) and logs one line per
    location. Pass the layers outermost first, as `LayerKinds.order` gives them."""

    def __init__(self, layers: Sequence[Layered]):
        self.layers = list(layers)
        self._records: list[ResultRecord] = []

    def on_result(self, run: RunDir, record: ResultRecord) -> None:
        self._records.append(record)

    def on_finish(self, run: RunDir, outcome: RunOutcome) -> None:
        by_case: dict[tuple[str, str], dict[str, Verdict]] = {}
        for record in self._records:
            by_case.setdefault((record.case_id, record.variant), {})[record.target] = record.verdict
        failures = []
        for (case_id, variant), verdicts in sorted(by_case.items()):
            for where in LayerKinds.locate(self.layers, verdicts):
                failures.append(
                    {
                        "case_id": case_id,
                        "variant": variant,
                        "layer": where.layer,
                        "confirmed": where.confirmed,
                        "verdicts": {name: v.value for name, v in verdicts.items()},
                    }
                )
                label = case_id + (f"[{variant}]" if variant else "")
                qualifier = "" if where.confirmed else " or behind it"
                log.info("  %s starts at %s%s", label, where.layer, qualifier)
        run.write_json(
            RunFile.LAYERS,
            {
                "schema": Schema.LAYERS.value,
                "stack": [
                    {"name": layer.name, "kind": layer.kind, "behind": layer.behind}
                    for layer in self.layers
                ],
                "failures": failures,
            },
        )
