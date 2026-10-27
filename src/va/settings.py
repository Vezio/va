"""Environment settings shared by CronJobs, CI workflows and interactive use.

Each application has one prefix, its name in upper snake case, and each setting is read from the
environment variable `<PREFIX>_<KEY>`. Command-line flags take their defaults from these
settings, so that, for example, `--remote-output` and `MYTOOL_RESULTS_URI` configure the same
option. A CronJob, a CI workflow and a person at a terminal therefore configure a run in the
same way.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class Key(StrEnum):
    """Names of the settings the framework reads from the environment.

    Attributes:
        PROFILE: Path of the profile file.
        WORK_DIR: Directory in which run directories are created.
        RESULTS_URI: Location where runs are published (`file://`, `s3://` or a plugin scheme).
        FALLBACK_URI: Location used by `job` when `RESULTS_URI` cannot be used.
        GROUP: Archive partition, such as a profile, a model or a suite.
        RUN_BUDGET: Time in seconds after which no new case is started.
        NOTIFY: Comma-separated notifier specifications, e.g. `slack:env:SLACK_WEBHOOK`.
        BASELINE_LABEL: Label that identifies baseline runs, e.g. `trigger=nightly`.
        WORKERS: Number of cases run concurrently. Optional; the default is 1.
        CASE_TIMEOUT: Time in seconds after which a case is abandoned and reported as an error.
            Optional.
        METRICS_FILE: Path of a Prometheus text file, e.g. for a textfile collector. Optional.
    """

    PROFILE = "PROFILE"
    WORK_DIR = "WORK_DIR"
    RESULTS_URI = "RESULTS_URI"
    FALLBACK_URI = "FALLBACK_URI"
    GROUP = "GROUP"
    RUN_BUDGET = "RUN_BUDGET"
    NOTIFY = "NOTIFY"
    BASELINE_LABEL = "BASELINE_LABEL"
    WORKERS = "WORKERS"
    CASE_TIMEOUT = "CASE_TIMEOUT"
    METRICS_FILE = "METRICS_FILE"


@dataclass(frozen=True)
class Settings:
    """Typed access to prefixed settings in an environment mapping.

    Empty and whitespace-only values are treated as unset.

    Attributes:
        prefix: Prefix prepended to every key, without the trailing underscore.
        environ: Mapping the values are read from. Defaults to `os.environ`.
    """

    prefix: str
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)

    @classmethod
    def for_app(cls, prog: str, environ: Mapping[str, str] | None = None) -> Settings:
        """Create settings whose prefix is derived from a program name.

        Args:
            prog: Program name. It is upper-cased and each run of characters other than
                letters and digits is replaced by an underscore.
            environ: Mapping to read from. Defaults to `os.environ`.

        Returns:
            The settings for the application.
        """
        prefix = re.sub(r"[^A-Z0-9]+", "_", prog.upper()).strip("_")
        return cls(prefix, environ if environ is not None else os.environ)

    def name(self, key: Key | str) -> str:
        """Return the environment variable name for a key.

        Args:
            key: Setting key.

        Returns:
            The name in the form `<PREFIX>_<KEY>`.
        """
        return f"{self.prefix}_{key}"

    def get(self, key: Key | str, default: str | None = None) -> str | None:
        """Return a setting as a stripped string.

        Args:
            key: Setting key.
            default: Value returned when the setting is unset or blank.

        Returns:
            The stripped value, or `default` when the setting is unset or blank.
        """
        value = self.environ.get(self.name(key), "").strip()
        return value or default

    def get_float(self, key: Key | str, default: float | None = None) -> float | None:
        """Return a setting parsed as a float.

        Args:
            key: Setting key.
            default: Value returned when the setting is unset or blank.

        Returns:
            The parsed value, or `default` when the setting is unset or blank.

        Raises:
            ValueError: If the value is not a number.
        """
        value = self.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"{self.name(key)} must be a number, not {value!r}") from exc

    def get_int(self, key: Key | str, default: int | None = None) -> int | None:
        """Return a setting parsed as an integer.

        Args:
            key: Setting key.
            default: Value returned when the setting is unset or blank.

        Returns:
            The parsed value, or `default` when the setting is unset or blank.

        Raises:
            ValueError: If the value is not a whole number.
        """
        value = self.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{self.name(key)} must be a whole number, not {value!r}") from exc

    def get_list(self, key: Key | str) -> list[str]:
        """Return a comma-separated setting as a list.

        Args:
            key: Setting key.

        Returns:
            The stripped, non-empty items, or an empty list when the setting is unset.
        """
        return [v.strip() for v in (self.get(key) or "").split(",") if v.strip()]
