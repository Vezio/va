"""The modelcheck profile: an OpenAI-compatible serving stack, declared layer by layer.

    name: demo-stack
    model: demo-model              # defaults for every target...
    max_latency_ms: 800
    targets:
      - name: gateway
        kind: proxy                # proxy, router or engine: see layers.py
        behind: router             # the target this layer forwards to
        base_url_env: GATEWAY_URL  # a variable's name, never the URL
        key_env: GATEWAY_TOKEN     # a variable's name, never the key
        capabilities: [models, chat, stream, auth]
        max_latency_ms: 1500       # ...which a target may override
        expect:
          unknown_model_status: 404  # what a correct layer answers

Targets are layers: each has a kind, and names the target it is behind. They are validated as a
stack (no cycle, no layer in front of an outer kind) and run outermost first, whatever order the
file lists them in.

A profile declares what a **correct** stack does, per layer; the run measures it. When a layer
differs, the case fails and the difference is the finding. Do not edit a declaration to make a
run pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from va import profile
from va.profile import Problems

from .layers import KINDS

CAPABILITIES = ("models", "chat", "stream", "auth")
TOP_KEYS = ("name", "description", "model", "max_latency_ms", "targets")
TARGET_KEYS = (
    "name",
    "kind",
    "behind",
    "base_url_env",
    "key_env",
    "capabilities",
    "model",
    "max_latency_ms",
    "expect",
)
EXPECT_KEYS = ("unknown_model_status", "bad_key_status")


@dataclass(frozen=True)
class Expect:
    """Behaviour declared for one layer: what a correct one answers."""

    unknown_model_status: int = 404
    bad_key_status: int = 401


@dataclass(frozen=True)
class Target:
    name: str
    kind: str
    behind: str | None
    base_url_env: str
    key_env: str | None
    capabilities: frozenset[str]
    model: str
    max_latency_ms: int
    expect: Expect = field(default_factory=Expect)

    @property
    def base_url(self) -> str:
        return (profile.resolve_env(self.base_url_env) or "").rstrip("/")


@dataclass(frozen=True)
class Profile:
    name: str
    targets: tuple[Target, ...]
    digest: str
    source: Path


def load(path: Path) -> Profile:
    """Read and validate a profile; every problem is reported at once (exit 2)."""
    data = profile.load(path)
    p = Problems()
    p.unknown_keys(data, TOP_KEYS)
    name = p.require(data, "name", str)
    default_model = p.optional(data, "model", str)
    default_latency = p.int_range(
        p.optional(data, "max_latency_ms", int, 2000), 1, 600_000, "max_latency_ms"
    )
    targets = [
        _target(p, raw, f"targets[{i}]", default_model, default_latency)
        for i, raw in enumerate(p.require(data, "targets", list) or [])
    ]
    names = [t.name for t in targets if t]
    if len(set(names)) != len(names):
        p.add("targets", "names must be unique")
    if not targets:
        p.add("targets", "declare at least one target")
    declared = [t for t in targets if t]
    if len(declared) == len(targets):
        KINDS.check_stack(p, declared)
    p.raise_if_any(str(path))
    return Profile(name, tuple(KINDS.order(declared)), profile.digest(data), path)


def _target(
    p: Problems, raw: Any, at: str, model: str | None, latency: int | None
) -> Target | None:
    if not isinstance(raw, dict):
        p.add(at, "must be a table")
        return None
    p.unknown_keys(raw, TARGET_KEYS, at=at)
    kind, behind = KINDS.parse(p, raw, at)
    capabilities = p.optional(raw, "capabilities", list, [], at=at) or []
    for cap in capabilities:
        p.one_of(cap, CAPABILITIES, f"{at}.capabilities")
    key_env = p.env_name(p.optional(raw, "key_env", str, at=at), f"{at}.key_env")
    if "auth" in capabilities and not key_env:
        p.add(f"{at}.key_env", "is required when the target declares auth")
    target_model = p.optional(raw, "model", str, model, at=at)
    if target_model is None:
        p.add(f"{at}.model", "is required (here or at the top of the profile)")
    expect_raw = p.optional(raw, "expect", dict, {}, at=at) or {}
    p.unknown_keys(expect_raw, EXPECT_KEYS, at=f"{at}.expect")
    expect = Expect(
        unknown_model_status=p.int_range(
            expect_raw.get("unknown_model_status", 404),
            100,
            599,
            f"{at}.expect.unknown_model_status",
        )
        or 404,
        bad_key_status=p.int_range(
            expect_raw.get("bad_key_status", 401), 100, 599, f"{at}.expect.bad_key_status"
        )
        or 401,
    )
    return Target(
        name=p.require(raw, "name", str, at=at) or at,
        kind=kind,
        behind=behind,
        base_url_env=p.env_name(p.require(raw, "base_url_env", str, at=at), f"{at}.base_url_env")
        or "",
        key_env=key_env,
        capabilities=frozenset(c for c in capabilities if c in CAPABILITIES),
        model=target_model or "",
        max_latency_ms=p.int_range(
            p.optional(raw, "max_latency_ms", int, latency, at=at),
            1,
            600_000,
            f"{at}.max_latency_ms",
        )
        or 2000,
        expect=expect,
    )
