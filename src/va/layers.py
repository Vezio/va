"""Layered targets that represent the successive ingresses of one serving stack.

A serving stack can be reached at several points, such as a gateway, a router behind it and an
engine behind the router. When targets are declared as layers, each carries a kind and,
optionally, the name of the target it is behind (forwards requests to). The framework then:

- validates the declaration: every kind is registered, every `behind` names another target,
  there is no cycle, and no layer is in front of a layer of an outer kind;
- orders the targets outermost first, so that columns, reports and listings read from the
  client side inwards;
- associates an adapter with each kind, which handles what differs when calling that kind of
  layer, such as required headers or how the answering backend is identified. Cases remain
  generic;
- locates failures: when a case fails at one layer and passes at the layer behind it, the defect
  is in the outer layer. The `LayerLocator` run hook does this for every failing case and writes
  `layers.json`.

Kinds are defined by the tool, not by the framework. A tool creates one `LayerKinds`, registers
its kinds with their depth, and uses it to parse profiles. This module holds no global state.

Example:
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
    """Protocol for a target declared as a layer.

    `LayerKinds.locate` treats a layer whose `behind` is None as the innermost layer, so
    implementations must set `behind` on every layer that is in front of another.
    """

    @property
    def name(self) -> str:
        """The target name, unique within the stack."""
        ...

    @property
    def kind(self) -> str:
        """The name of the layer kind, as registered with `LayerKinds`."""
        ...

    @property
    def behind(self) -> str | None:
        """The name of the target this layer forwards to, or None for the innermost layer."""
        ...


L = TypeVar("L", bound=Layered)


class LayerAdapter:
    """Behaviour specific to calling one kind of layer.

    Subclasses override only the methods whose behaviour differs. The defaults suit a layer that
    requires no extra headers and does not report which component answered.
    """

    def request_headers(self, layer: Layered) -> dict[str, str]:
        """Return the headers that every request to this layer carries.

        Such headers can, for example, route a request to a particular backend.

        Args:
            layer: The layer being called.

        Returns:
            The headers to add. The default is an empty mapping.
        """
        return {}

    def served_by(self, layer: Layered, headers: Mapping[str, str]) -> str | None:
        """Return the component that answered, as reported in the response headers.

        The value is recorded as evidence with each exchange. It does not determine a verdict.

        Args:
            layer: The layer that was called.
            headers: The response headers.

        Returns:
            The name of the answering component, or None when the layer does not report it.
        """
        return None


class HeaderServedBy(LayerAdapter):
    """Adapter for a layer that names the answering component in a response header.

    Args:
        header: Name of the response header. Matched case-insensitively.

    Attributes:
        header: Lower-cased name of the response header.
    """

    def __init__(self, header: str):
        self.header = header.lower()

    def served_by(self, layer: Layered, headers: Mapping[str, str]) -> str | None:
        """Return the value of the configured header, or None when it is absent."""
        for key, value in headers.items():
            if key.lower() == self.header:
                return value
        return None


class AnswersItself(LayerAdapter):
    """Adapter for the innermost layer, which is itself the component that answers."""

    def served_by(self, layer: Layered, headers: Mapping[str, str]) -> str | None:
        """Return the layer's own name."""
        return layer.name


@dataclass(frozen=True)
class LayerKind:
    """A registered kind of layer.

    Attributes:
        name: Name of the kind.
        depth: Position from the client inwards. Depth 0 is the layer called first.
        description: Human-readable description.
        adapter: Adapter used when calling layers of this kind.
    """

    name: str
    depth: int
    description: str = ""
    adapter: LayerAdapter = field(default_factory=LayerAdapter)


@dataclass(frozen=True)
class Location:
    """The layer at which a failure starts.

    Attributes:
        layer: Name of the layer.
        confirmed: Whether the layer behind it passed the case. False when the layer behind it
            did not run the case (it was skipped or not declared), in which case the defect may
            be further in.
    """

    layer: str
    confirmed: bool


class LayerKinds:
    """Registry of a tool's layer kinds, with operations over a declared stack."""

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
        """Register a layer kind.

        Args:
            name: Name of the kind.
            depth: Position from the client inwards. Depth 0 is the layer called first.
            description: Human-readable description.
            adapter: Adapter for layers of this kind. Defaults to a plain `LayerAdapter`.

        Returns:
            The registered kind.

        Raises:
            ValueError: If a kind with the same name is already registered.
        """
        if name in self._kinds:
            raise ValueError(f"layer kind {name!r} is already registered")
        kind = LayerKind(name, depth, description, adapter or LayerAdapter())
        self._kinds[name] = kind
        return kind

    def __contains__(self, name: object) -> bool:
        """Return whether a kind with the given name is registered."""
        return name in self._kinds

    def __getitem__(self, name: str) -> LayerKind:
        """Return the registered kind with the given name."""
        return self._kinds[name]

    @property
    def names(self) -> tuple[str, ...]:
        """The registered kind names, outermost first."""
        return tuple(k.name for k in sorted(self._kinds.values(), key=lambda k: k.depth))

    def adapter(self, layer: Layered) -> LayerAdapter:
        """Return the adapter for a layer's kind.

        Args:
            layer: The layer.

        Returns:
            The adapter registered for the layer's kind.
        """
        return self._kinds[layer.kind].adapter

    def parse(self, p: Problems, raw: Mapping, at: str) -> tuple[str, str | None]:
        """Read the `kind` and `behind` fields from one target's profile table.

        `kind` is required and must be a registered kind. `behind` is optional. Problems are
        collected in `p` rather than raised. Once every target is read, validate the whole stack
        with `check_stack`.

        Args:
            p: Collector for validation problems.
            raw: The target's table from the profile.
            at: Path of the table within the profile, used in problem messages.

        Returns:
            The kind (an empty string if it is invalid) and the `behind` value.
        """
        kind = p.one_of(p.require(raw, "kind", str, at=at), self.names, f"{at}.kind")
        behind = p.optional(raw, "behind", str, at=at)
        return (kind if kind in self._kinds else ""), behind

    def check_stack(self, p: Problems, layers: Sequence[Layered], at: str = "targets") -> None:
        """Validate the relationships between declared layers.

        Each `behind` must name another target, no layer may be in front of a layer of an outer
        kind, and following `behind` from any layer must terminate.

        Args:
            p: Collector for validation problems.
            layers: The declared layers, in profile order.
            at: Path of the targets list within the profile, used in problem messages.
        """
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

    def order(self, layers: Iterable[L]) -> list[L]:
        """Return the layers sorted outermost first.

        Layers of the same kind keep their declared order.

        Args:
            layers: The layers to sort.

        Returns:
            The sorted layers.
        """
        return sorted(layers, key=lambda layer: self._kinds[layer.kind].depth)

    @staticmethod
    def chain(layers: Sequence[L], name: str) -> list[L]:
        """Return the named layer followed by every layer behind it, from the outside inwards.

        Args:
            layers: The declared layers.
            name: Name of the first layer.

        Returns:
            The chain of layers, or an empty list if no layer has that name.
        """
        by_name = {layer.name: layer for layer in layers}
        out: list[L] = []
        current = by_name.get(name)
        while current is not None and current not in out:
            out.append(current)
            current = by_name.get(current.behind or "")
        return out

    @staticmethod
    def locate(layers: Sequence[Layered], verdicts: Mapping[str, Verdict | str]) -> list[Location]:
        """Return the layers at which one case's failures start.

        A failing layer whose `behind` layer passed is a confirmed location of the defect. A
        failing layer behind which nothing ran is the outermost possible location. A failure that
        only repeats a failure from a layer further in is not reported again.

        Args:
            layers: The declared layers.
            verdicts: The case's verdict for each layer name that ran it.

        Returns:
            One location per independent failure.
        """
        failing = {Verdict.FAIL, Verdict.ERROR}

        def failed(name: str) -> bool:
            """Return whether the named layer failed or errored."""
            return Verdict(verdicts[name]) in failing

        out = []
        for layer in layers:
            if layer.name not in verdicts or not failed(layer.name):
                continue
            behind = layer.behind
            if behind is not None and behind in verdicts and failed(behind):
                # The failure repeats one from a layer further in.
                continue
            passed_behind = (
                behind is not None
                and behind in verdicts
                and Verdict(verdicts[behind]) is Verdict.PASS
            )
            out.append(Location(layer.name, confirmed=behind is None or passed_behind))
        return out


class LayerLocator(RunHooks):
    """Run hook that reports where each failing case's failure starts.

    At the end of the run, it writes `layers.json`, containing the declared stack and one entry
    per failure location, and logs one line per location.

    Args:
        layers: The declared layers, outermost first, as returned by `LayerKinds.order`.

    Attributes:
        layers: The declared layers, outermost first.
    """

    def __init__(self, layers: Sequence[Layered]):
        self.layers = list(layers)
        self._records: list[ResultRecord] = []

    def on_result(self, run: RunDir, record: ResultRecord) -> None:
        """Collect a result record for analysis at the end of the run."""
        self._records.append(record)

    def on_finish(self, run: RunDir, outcome: RunOutcome) -> None:
        """Locate each failure, log it and write `layers.json` to the run directory."""
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
