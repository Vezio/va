"""The httpcheck profile: which HTTP services to check and what a correct one does.

    name: my-service
    targets:
      - name: gateway
        base_url_env: GATEWAY_URL   # a variable name, never the URL itself
        key_env: GATEWAY_TOKEN      # optional; read through REDACTOR.secret_from_env
        capabilities: [health, json, auth]
        health_path: /health
        max_latency_ms: 500

Declare what a correct service does. A target that differs fails, and the team triages it;
the profile is not edited to make the run green.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from va import profile
from va.profile import Problems

CAPABILITIES = ("health", "json", "auth")


@dataclass(frozen=True)
class Target:
    name: str
    base_url_env: str
    key_env: str | None = None
    capabilities: frozenset[str] = frozenset()
    health_path: str = "/health"
    max_latency_ms: int = 1000

    @property
    def base_url(self) -> str:
        return (profile.resolve_env(self.base_url_env) or "").rstrip("/")


@dataclass(frozen=True)
class Profile:
    name: str
    targets: tuple[Target, ...]
    digest: str
    source: Path = field(default=Path("."))


def load(path: Path) -> Profile:
    data = profile.load(path)
    p = Problems()
    name = p.require(data, "name", str)
    targets = []
    for i, raw in enumerate(p.require(data, "targets", list) or []):
        at = f"targets[{i}]"
        caps = p.optional(raw, "capabilities", list, [], at=at) or []
        for cap in caps:
            p.one_of(cap, CAPABILITIES, f"{at}.capabilities")
        target = Target(
            name=p.require(raw, "name", str, at=at) or f"target{i}",
            # An invalid value is recorded as a problem; "" only fills the slot until
            # raise_if_any reports it.
            base_url_env=p.env_name(
                p.require(raw, "base_url_env", str, at=at), f"{at}.base_url_env"
            )
            or "",
            key_env=p.env_name(p.optional(raw, "key_env", str, at=at), f"{at}.key_env"),
            capabilities=frozenset(caps),
            health_path=p.optional(raw, "health_path", str, "/health", at=at),
            max_latency_ms=p.optional(raw, "max_latency_ms", int, 1000, at=at),
        )
        if "auth" in target.capabilities and not target.key_env:
            p.add(f"{at}.key_env", "is required when the target declares auth")
        targets.append(target)
    if len({t.name for t in targets}) != len(targets):
        p.add("targets", "names must be unique")
    p.raise_if_any(str(path))
    return Profile(name, tuple(targets), profile.digest(data), path)
