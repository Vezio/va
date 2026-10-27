"""Profiles: declare what the target is and should do; validate offline, report every problem.

A profile states what a **correct** target does, never a description fitted to today's
behaviour. The run is the measurement: a declaration the target does not honour fails.

- **No secrets and no hosts in a committed profile.** It names environment variables
  (`base_url_env`, `key_env`); real detail lives in `*.local.*` files that Git ignores.
- **Validation reports every problem with its path**, in one pass. An invalid profile is a
  usage error (exit 2), never a test failure.

The file format is a plugin: TOML and JSON are built in, YAML with the `yaml` extra, and
another (Jsonnet, CUE, HCL) is a loader registered for its suffix.

`Problems` is the collector an app uses to build its own typed profile from the loaded data:

    p = Problems()
    name = p.require(data, "name", str)
    for i, t in enumerate(p.require(data, "targets", list) or []):
        p.require(t, "base_url_env", str, at=f"targets[{i}]")
    p.raise_if_any(path)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import Any

from .plugins import PLUGINS, UnknownPlugin


class ProfileError(Exception):
    def __init__(self, source: str, problems: list[str]):
        super().__init__(f"{source}: {len(problems)} problem(s):\n  " + "\n  ".join(problems))
        self.source = source
        self.problems = problems


Loader = Callable[[str], Any]  # file text -> data


def _yaml(text: str) -> Any:
    try:
        import yaml  # noqa: PLC0415 -- optional, the yaml extra
    except ImportError as exc:
        raise ValueError("YAML profiles need PyYAML (the yaml extra)") from exc
    return yaml.safe_load(text)


PLUGINS.profile_loaders.register("toml", tomllib.loads)
PLUGINS.profile_loaders.register("json", json.loads)
PLUGINS.profile_loaders.register("yaml", _yaml)
PLUGINS.profile_loaders.register("yml", _yaml)


def load(path: Path) -> dict[str, Any]:
    """Read a profile file into a mapping; the format follows the suffix."""
    suffix = path.suffix.lower().lstrip(".")
    try:
        loader = PLUGINS.profile_loaders.get(suffix)
    except UnknownPlugin as exc:
        raise ProfileError(str(path), [str(exc)]) from None
    try:
        data = loader(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileError(str(path), [f"cannot read: {exc}"]) from exc
    except Exception as exc:  # noqa: BLE001 -- every parser raises its own type
        raise ProfileError(str(path), [f"not parseable: {exc}"]) from exc
    if not isinstance(data, dict):
        raise ProfileError(str(path), ["the top level must be a mapping"])
    return data


def digest(data: Mapping[str, Any], ignore: Collection[str] = ()) -> str:
    """A stable hash of the declarations, recorded on every run so a comparison can say "the
    profile changed" before listing the cases that moved. `ignore` drops keys that label
    results without changing what is declared."""
    kept = {k: v for k, v in data.items() if k not in ignore}
    return hashlib.sha256(json.dumps(kept, sort_keys=True, default=str).encode()).hexdigest()[:16]


class Problems:
    """Collects every problem with its path; typed accessors return None on a problem so
    validation continues and reports the rest."""

    ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, path: str, message: str) -> None:
        self.items.append(f"{path}: {message}" if path else message)

    def require(self, data: Any, key: str, kind: type | tuple[type, ...], *, at: str = "") -> Any:
        path = f"{at}.{key}" if at else key
        if not isinstance(data, Mapping) or key not in data:
            self.add(path, "is required")
            return None
        return self.check_type(data[key], kind, path)

    def optional(
        self,
        data: Any,
        key: str,
        kind: type | tuple[type, ...],
        default: Any = None,
        *,
        at: str = "",
    ) -> Any:
        if not isinstance(data, Mapping) or key not in data:
            return default
        return self.check_type(data[key], kind, f"{at}.{key}" if at else key)

    def check_type(self, value: Any, kind: type | tuple[type, ...], path: str) -> Any:
        kinds = kind if isinstance(kind, tuple) else (kind,)
        # bool is an int in Python; a profile that says `true` for a number is a mistake.
        if not isinstance(value, kinds) or (isinstance(value, bool) and bool not in kinds):
            names = " or ".join(k.__name__ for k in kinds)
            self.add(path, f"must be {names}, not {type(value).__name__}")
            return None
        return value

    def one_of(self, value: Any, allowed: Collection[str], path: str) -> Any:
        if value is not None and value not in allowed:
            self.add(path, f"must be one of {', '.join(map(str, allowed))}, not {value!r}")
            return None
        return value

    def env_name(self, value: Any, path: str) -> str | None:
        """A field that names a variable must hold a name, never the value itself."""
        if value is None:
            return None
        if not isinstance(value, str) or not self.ENV_NAME.match(value):
            self.add(path, "must name an environment variable (UPPER_SNAKE), not hold a value")
            return None
        return value

    def raise_if_any(self, source: str) -> None:
        if self.items:
            raise ProfileError(source, self.items)


def resolve_env(name: str | None, environ: Mapping[str, str] | None = None) -> str | None:
    """The value of a variable a profile names. Secrets go through
    `REDACTOR.secret_from_env` instead, so they are registered for redaction."""
    return (environ if environ is not None else os.environ).get(name) if name else None
