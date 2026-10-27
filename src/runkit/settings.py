"""Settings: every option a CronJob or CI sets through the environment, named in one place.

An app has one prefix (its name in UPPER_SNAKE); each setting is `<PREFIX>_<KEY>`. Flags read
their defaults from here, so `--remote-output` and `MYTOOL_RESULTS_URI` are the same option,
and the CronJob, the CI workflow and a person at a terminal configure a run the same way.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class Key(StrEnum):
    PROFILE = "PROFILE"  # the profile file
    WORK_DIR = "WORK_DIR"  # where run directories are created
    RESULTS_URI = "RESULTS_URI"  # where runs are published (file://, s3://, a plugin scheme)
    FALLBACK_URI = "FALLBACK_URI"  # used by `job` when RESULTS_URI is unusable here
    GROUP = "GROUP"  # the archive partition (a profile, a model, a suite)
    RUN_BUDGET = "RUN_BUDGET"  # seconds after which no new case starts
    NOTIFY = "NOTIFY"  # comma-separated notifier specs, e.g. slack:env:SLACK_WEBHOOK
    BASELINE_LABEL = "BASELINE_LABEL"  # which runs are baselines, e.g. trigger=nightly
    WORKERS = "WORKERS"  # opt-in: cases run concurrently (default 1)
    CASE_TIMEOUT = "CASE_TIMEOUT"  # opt-in: seconds before a case is abandoned as an error
    METRICS_FILE = "METRICS_FILE"  # opt-in: Prometheus text file, e.g. a textfile collector


@dataclass(frozen=True)
class Settings:
    prefix: str
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)

    @classmethod
    def for_app(cls, prog: str, environ: Mapping[str, str] | None = None) -> Settings:
        prefix = re.sub(r"[^A-Z0-9]+", "_", prog.upper()).strip("_")
        return cls(prefix, environ if environ is not None else os.environ)

    def name(self, key: Key | str) -> str:
        return f"{self.prefix}_{key}"

    def get(self, key: Key | str, default: str | None = None) -> str | None:
        value = self.environ.get(self.name(key), "").strip()
        return value or default

    def get_float(self, key: Key | str, default: float | None = None) -> float | None:
        value = self.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"{self.name(key)} must be a number, not {value!r}") from exc

    def get_int(self, key: Key | str, default: int | None = None) -> int | None:
        value = self.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{self.name(key)} must be a whole number, not {value!r}") from exc

    def get_list(self, key: Key | str) -> list[str]:
        return [v.strip() for v in (self.get(key) or "").split(",") if v.strip()]
