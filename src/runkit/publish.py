"""Publish a finished run directory, manifest last; read published runs back.

The layout is `<destination>/<group>/<run id>/<files>`. `group` partitions the archive for
comparison: a profile name, a model, a suite. A run is complete in storage exactly when its
`MANIFEST.json` is there: it is written after every other file and uploaded last, so an
`Archive` lists only complete runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import partial

from . import retry
from .log import get_logger
from .model import Publication, PublicationStatus, ResultRecord, RunOutcome
from .redact import REDACTOR
from .rundir import MANIFEST, OUTCOME, RESULTS, RunDir
from .sinks import Sink, sink_for

log = get_logger("publish")


def deliver(
    run: RunDir,
    destination: str | Sink,
    group: str,
    policy: retry.RetryPolicy = retry.DEFAULT_POLICY,
) -> Publication:
    """Publish `run` under `<destination>/<group>/<run id>/`, the manifest last. Never raises:
    the Publication says published, skipped (with why) or failed (with the error)."""
    uri = destination if isinstance(destination, str) else destination.uri
    try:
        sink = sink_for(destination) if isinstance(destination, str) else destination
        reason = sink.unavailable_reason()
        if reason:
            log.warning("publication skipped: %s; results stay local", reason)
            return Publication(PublicationStatus.SKIPPED, uri, reason=reason)
        base = f"{group}/{run.run_id}"
        for rel in run.write_manifest():  # the manifest is the last entry
            retry.call(
                partial(sink.put, run.file(rel), f"{base}/{rel}"),
                what=f"upload {rel}",
                policy=policy,
            )
        location = sink.location(base) + "/"
        log.info("published to %s", location)
        return Publication(PublicationStatus.PUBLISHED, uri, location)
    except Exception as exc:  # noqa: BLE001 -- any delivery error means "not published"
        reason = REDACTOR.scrub(str(exc))
        log.error("publication failed: %s", reason)
        return Publication(PublicationStatus.FAILED, uri, reason=reason)


@dataclass
class Archive:
    """Published runs under one destination, read back."""

    sink: Sink

    @classmethod
    def at(cls, uri: str) -> Archive:
        return cls(sink_for(uri))

    def runs(self, group: str) -> list[str]:
        """Complete run ids of `group`, oldest first. A run without its manifest is still
        uploading, or died mid-upload, and is never a baseline."""
        keys = set(self.sink.list(group))
        runs = {k.split("/")[1] for k in keys if k.count("/") >= 2}
        return sorted(r for r in runs if f"{group}/{r}/{MANIFEST}" in keys)

    def results(self, group: str, run_id: str) -> list[ResultRecord]:
        text = self.sink.get(f"{group}/{run_id}/{RESULTS}").decode("utf-8")
        lines = [line for line in text.splitlines() if line.strip()]
        return [ResultRecord.from_dict(json.loads(line)) for line in lines]

    def outcome(self, group: str, run_id: str) -> RunOutcome:
        return RunOutcome.from_dict(json.loads(self.sink.get(f"{group}/{run_id}/{OUTCOME}")))

    def baselines(
        self,
        group: str,
        *,
        exclude: str = "",
        window: int = 1,
        label: str | None = None,
        run_id: str | None = None,
    ) -> list[tuple[str, list[ResultRecord]]]:
        """The runs to compare with, oldest first: the one named `run_id`, or the latest
        `window` runs carrying `label` (KEY=VALUE, e.g. trigger=nightly), so an ad-hoc run
        never becomes the reference."""
        candidates = [r for r in self.runs(group) if r != exclude]
        if run_id:
            chosen = [r for r in candidates if r == run_id]
        else:
            if label:
                key, _, value = label.partition("=")
                candidates = [r for r in candidates if self._has_label(group, r, key, value)]
            chosen = candidates[-window:] if window > 0 else []
        return [(r, self.results(group, r)) for r in chosen]

    def _has_label(self, group: str, run_id: str, key: str, value: str) -> bool:
        try:
            return self.outcome(group, run_id).labels.get(key) == value
        except Exception:  # noqa: BLE001 -- an unreadable run is not a baseline
            return False
