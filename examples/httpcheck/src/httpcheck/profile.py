"""Profile model and loader for httpcheck.

A profile lists the HTTP services to check and declares how a correct service behaves. A
target that does not match its declaration fails the corresponding case. The failure is then
triaged; the profile is not changed to make the run pass.

Example:
    A minimal profile::

        name: my-service
        targets:
          - name: gateway
            base_url_env: GATEWAY_URL   # Name of the variable that holds the URL.
            key_env: GATEWAY_TOKEN      # Optional; read with REDACTOR.secret_from_env.
            capabilities: [health, json, auth]
            health_path: /health
            max_latency_ms: 500
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from va import profile
from va.profile import Problems

CAPABILITIES = ("health", "json", "auth")


@dataclass(frozen=True)
class Target:
    """One HTTP service to check.

    Attributes:
        name: Unique name of the target within the profile.
        base_url_env: Name of the environment variable that holds the base URL.
        key_env: Name of the environment variable that holds the bearer token, if any.
        capabilities: Declared capabilities, which select the cases that apply.
        health_path: Path of the health endpoint.
        max_latency_ms: Maximum accepted health response time, in milliseconds.
    """

    name: str
    base_url_env: str
    key_env: str | None = None
    capabilities: frozenset[str] = frozenset()
    health_path: str = "/health"
    max_latency_ms: int = 1000

    @property
    def base_url(self) -> str:
        """The base URL read from the environment, without a trailing slash, or `""`."""
        return (profile.resolve_env(self.base_url_env) or "").rstrip("/")


@dataclass(frozen=True)
class Profile:
    """A validated httpcheck profile.

    Attributes:
        name: Profile name, also used as the default result group.
        targets: Targets to check, in declaration order.
        digest: Digest of the profile content, recorded with each run.
        source: Path the profile was loaded from.
    """

    name: str
    targets: tuple[Target, ...]
    digest: str
    source: Path = field(default=Path("."))


def load(path: Path) -> Profile:
    """Load and validate a profile file.

    All problems are collected before any is reported, so that a single error lists every
    invalid field with its path.

    Args:
        path: Path to the profile file. The suffix selects the parser.

    Returns:
        The validated profile.

    Raises:
        va.profile.ProfileError: If the file cannot be read or parsed, or a field is invalid.
    """
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
            # An invalid value is recorded as a problem. The empty string is a placeholder
            # until raise_if_any reports it.
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
