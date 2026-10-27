"""Loading and validation of modelcheck profiles, which describe a serving stack per layer.

Example profile:

    name: demo-stack
    model: demo-model              # Default for every target.
    max_latency_ms: 800            # Default for every target; a target may override it.
    targets:
      - name: gateway
        kind: proxy                # One of proxy, router or engine (see layers.py).
        behind: router             # The target this layer forwards to.
        base_url_env: GATEWAY_URL  # The name of the variable holding the URL.
        key_env: GATEWAY_TOKEN     # The name of the variable holding the key.
        capabilities: [models, chat, stream, auth]
        max_latency_ms: 1500
        expect:
          unknown_model_status: 404  # The status a correct layer returns.

Each target is a layer with a kind and, optionally, the name of the target it is behind. The
targets are validated as a stack, which must contain no cycles and no layer in front of a kind
that is further out. They run outermost first, regardless of the order in the file.

A profile declares the behaviour of a correct stack for each layer, and the run measures the
deployed stack against it. When a layer behaves differently, the case fails and the difference
is reported as a finding. Declarations should not be edited to make a run pass.
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
    """Responses that a correct layer is declared to return.

    Attributes:
        unknown_model_status: The status for a request that names an unknown model.
        bad_key_status: The status for a request with an invalid API key.
    """

    unknown_model_status: int = 404
    bad_key_status: int = 401


@dataclass(frozen=True)
class Target:
    """One layer of the stack, as declared in the profile.

    Attributes:
        name: The target name, unique within the profile.
        kind: The layer kind: proxy, router or engine.
        behind: The name of the target this layer forwards to, if any.
        base_url_env: The name of the environment variable that holds the base URL.
        key_env: The name of the environment variable that holds the API key, if any.
        capabilities: The capabilities the layer declares, which select the cases that run.
        model: The model the layer serves.
        max_latency_ms: The latency budget for a completion, in milliseconds.
        expect: The responses a correct layer returns.
    """

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
        """The base URL from the environment, without a trailing slash, or an empty string."""
        return (profile.resolve_env(self.base_url_env) or "").rstrip("/")


@dataclass(frozen=True)
class Profile:
    """A validated profile.

    Attributes:
        name: The profile name.
        targets: The targets, ordered outermost first.
        digest: The digest of the profile data, which is independent of the file format.
        source: The file the profile was loaded from.
    """

    name: str
    targets: tuple[Target, ...]
    digest: str
    source: Path


def load(path: Path) -> Profile:
    """Read and validate a profile, reporting all problems together.

    Args:
        path: The profile file, in YAML, TOML or JSON.

    Returns:
        The validated profile, with its targets ordered outermost first.

    Raises:
        ProfileError: If the profile has any problems. The CLI exits with status 2.
    """
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
    """Validate one raw target entry and build a `Target` from it.

    Problems are recorded on `p` rather than raised.

    Args:
        p: The problem collector for the whole profile.
        raw: The raw target entry.
        at: The entry's path in the profile, used in problem messages.
        model: The profile's default model, if any.
        latency: The profile's default latency budget, in milliseconds.

    Returns:
        The target, or None if the entry is not a table.
    """
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
