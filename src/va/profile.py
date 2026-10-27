"""Loading and offline validation of profiles.

A profile declares what the target is and how a correct target behaves. It describes the
intended behaviour rather than the behaviour currently observed, and the run measures the target
against it: a declaration that the target does not honour results in a failure.

- A committed profile contains no secrets and no host names. It names environment variables
  (`base_url_env`, `key_env`), and the actual values are kept in `*.local.*` files that Git
  ignores.
- Validation reports all problems, each with its path, in a single pass. An invalid profile is a
  usage error (exit code 2) and is not reported as a test failure.

The file format is chosen by the file suffix. YAML (`.yaml`, `.yml`) is the default, TOML and
JSON are also supported, and other formats such as Jsonnet, CUE or HCL can be added by
registering a loader for their suffix.

An application uses `Problems` to build its own typed profile from the loaded data:

    p = Problems()
    name = p.require(data, "name", str)
    for i, t in enumerate(p.require(data, "targets", list) or []):
        p.require(t, "base_url_env", str, at=f"targets[{i}]")
    p.raise_if_any(path)
"""

from __future__ import annotations

import difflib
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
    """An error raised when a profile cannot be loaded or is invalid.

    Args:
        source: The profile file or other source the problems refer to.
        problems: A description of each problem, prefixed with its path where known.

    Attributes:
        source: The profile file or other source the problems refer to.
        problems: A description of each problem.
    """

    def __init__(self, source: str, problems: list[str]):
        super().__init__(f"{source}: {len(problems)} problem(s):\n  " + "\n  ".join(problems))
        self.source = source
        self.problems = problems


Loader = Callable[[str], Any]  # Parses the text of a profile file into data.


def _yaml(text: str) -> Any:
    """Parse YAML text with PyYAML, which is imported only when needed.

    Args:
        text: The YAML text.

    Returns:
        The parsed data.

    Raises:
        ValueError: If PyYAML is not installed.
    """
    try:
        # PyYAML is imported only when a YAML profile is loaded.
        import yaml  # noqa: PLC0415
    except ImportError as exc:
        raise ValueError("YAML profiles need PyYAML (the yaml extra)") from exc
    return yaml.safe_load(text)


PLUGINS.profile_loaders.register("toml", tomllib.loads)
PLUGINS.profile_loaders.register("json", json.loads)
PLUGINS.profile_loaders.register("yaml", _yaml)
PLUGINS.profile_loaders.register("yml", _yaml)


def load(path: Path) -> dict[str, Any]:
    """Read a profile file into a mapping, using the loader registered for its suffix.

    Args:
        path: The profile file.

    Returns:
        The top-level mapping of the profile.

    Raises:
        ProfileError: If no loader is registered for the suffix, the file cannot be read or
            parsed, or its top level is not a mapping.
    """
    suffix = path.suffix.lower().lstrip(".")
    try:
        loader = PLUGINS.profile_loaders.get(suffix)
    except UnknownPlugin as exc:
        raise ProfileError(str(path), [str(exc)]) from None
    try:
        data = loader(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileError(str(path), [f"cannot read: {exc}"]) from exc
    except Exception as exc:  # noqa: BLE001
        # Each format's parser raises its own exception type.
        raise ProfileError(str(path), [f"not parseable: {exc}"]) from exc
    if not isinstance(data, dict):
        raise ProfileError(str(path), ["the top level must be a mapping"])
    return data


def digest(data: Mapping[str, Any], ignore: Collection[str] = ()) -> str:
    """Return a stable hash of a profile's declarations.

    The digest is recorded for each run, so that a comparison can report that the profile
    changed before listing the cases whose results changed.

    Args:
        data: The profile data.
        ignore: Top-level keys to exclude, such as keys that only label results and do not
            change what is declared.

    Returns:
        The first 16 hex characters of the SHA-256 digest of the canonical JSON form.
    """
    kept = {k: v for k, v in data.items() if k not in ignore}
    return hashlib.sha256(json.dumps(kept, sort_keys=True, default=str).encode()).hexdigest()[:16]


class Problems:
    """A collector of validation problems, each recorded with its path.

    The typed accessors return None when they record a problem, so that validation can continue
    and report all remaining problems.

    Attributes:
        ENV_NAME: The pattern a valid environment variable name must match.
        items: The problems recorded so far.
    """

    ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, path: str, message: str) -> None:
        """Record a problem.

        Args:
            path: The path of the offending value, or an empty string.
            message: A description of the problem.
        """
        self.items.append(f"{path}: {message}" if path else message)

    def require(self, data: Any, key: str, kind: type | tuple[type, ...], *, at: str = "") -> Any:
        """Return a required value, checking its type.

        Args:
            data: The mapping expected to contain the key.
            key: The required key.
            kind: The accepted type or types.
            at: The path of `data`, used as a prefix in problem paths.

        Returns:
            The value, or None if it is missing or has the wrong type.
        """
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
        """Return an optional value, checking its type if it is present.

        Args:
            data: The mapping that may contain the key.
            key: The optional key.
            kind: The accepted type or types.
            default: The value returned if the key is absent.
            at: The path of `data`, used as a prefix in problem paths.

        Returns:
            The value, `default` if it is absent, or None if it has the wrong type.
        """
        if not isinstance(data, Mapping) or key not in data:
            return default
        return self.check_type(data[key], kind, f"{at}.{key}" if at else key)

    def check_type(self, value: Any, kind: type | tuple[type, ...], path: str) -> Any:
        """Check that a value has one of the accepted types.

        A boolean is rejected unless `bool` is accepted explicitly.

        Args:
            value: The value to check.
            kind: The accepted type or types.
            path: The path of the value, used in the problem message.

        Returns:
            The value, or None if it has the wrong type.
        """
        kinds = kind if isinstance(kind, tuple) else (kind,)
        # In Python bool is a subclass of int, so `true` would otherwise be accepted as a number.
        if not isinstance(value, kinds) or (isinstance(value, bool) and bool not in kinds):
            names = " or ".join(k.__name__ for k in kinds)
            self.add(path, f"must be {names}, not {type(value).__name__}")
            return None
        return value

    def one_of(self, value: Any, allowed: Collection[str], path: str) -> Any:
        """Check that a value is one of the allowed values.

        Args:
            value: The value to check. None is accepted without a problem.
            allowed: The allowed values.
            path: The path of the value, used in the problem message.

        Returns:
            The value, or None if it is not allowed.
        """
        if value is not None and value not in allowed:
            self.add(path, f"must be one of {', '.join(map(str, allowed))}, not {value!r}")
            return None
        return value

    def env_name(self, value: Any, path: str) -> str | None:
        """Check that a value is the name of an environment variable rather than its value.

        Args:
            value: The value to check. None is accepted without a problem.
            path: The path of the value, used in the problem message.

        Returns:
            The name, or None if it is missing or not an upper-case variable name.
        """
        if value is None:
            return None
        if not isinstance(value, str) or not self.ENV_NAME.match(value):
            self.add(path, "must name an environment variable (UPPER_SNAKE), not hold a value")
            return None
        return value

    def unknown_keys(self, data: Any, allowed: Collection[str], *, at: str = "") -> None:
        """Record a problem for each key that the schema does not define.

        Each problem suggests the closest known key, if there is one. Without this check a
        misspelled key would be ignored and the profile would declare less than intended.

        Args:
            data: The mapping to check. Values that are not mappings are ignored.
            allowed: The keys the schema defines.
            at: The path of `data`, used as a prefix in problem paths.
        """
        if not isinstance(data, Mapping):
            return
        for key in data:
            if key in allowed:
                continue
            close = difflib.get_close_matches(str(key), list(allowed), n=1)
            hint = f"; did you mean {close[0]!r}?" if close else ""
            self.add(f"{at}.{key}" if at else str(key), f"is not a known key{hint}")

    def int_range(self, value: Any, low: int, high: int, path: str) -> int | None:
        """Check that a value is an integer within an inclusive range.

        Args:
            value: The value to check. None is accepted without a problem.
            low: The smallest allowed value.
            high: The largest allowed value.
            path: The path of the value, used in the problem message.

        Returns:
            The value, or None if it is missing, not an integer or out of range.
        """
        if value is None:
            return None
        if self.check_type(value, int, path) is None:
            return None
        if not low <= value <= high:
            self.add(path, f"must be between {low} and {high}, not {value}")
            return None
        return value

    def raise_if_any(self, source: str) -> None:
        """Raise a `ProfileError` if any problem has been recorded.

        Args:
            source: The profile file or other source the problems refer to.

        Raises:
            ProfileError: If at least one problem has been recorded.
        """
        if self.items:
            raise ProfileError(source, self.items)


def resolve_env(name: str | None, environ: Mapping[str, str] | None = None) -> str | None:
    """Return the value of an environment variable named by a profile.

    Secrets should be read with `REDACTOR.secret_from_env` instead, so that they are registered
    for redaction.

    Args:
        name: The variable name, or None.
        environ: The environment to read. Defaults to `os.environ`.

    Returns:
        The value, or None if `name` is None or the variable is not set.
    """
    return (environ if environ is not None else os.environ).get(name) if name else None
