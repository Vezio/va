"""Publication of finished run directories and retrieval of published runs.

Runs are stored as `<destination>/<group>/<run id>/<files>`. The group partitions the archive
for comparison, typically by profile name, model or suite. A run is complete in storage when
its `MANIFEST.json` is present. The manifest is written after every other file and uploaded
last, so `Archive` lists only runs whose upload finished.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import partial

from . import retry
from .defaults import DEFAULTS, RetryPolicy
from .layout import RunFile
from .log import get_logger
from .model import Publication, PublicationStatus, ResultRecord, RunOutcome
from .redact import REDACTOR
from .rundir import RunDir
from .sinks import Sink, sink_for

log = get_logger("publish")


def deliver(
    run: RunDir,
    destination: str | Sink,
    group: str,
    policy: RetryPolicy = DEFAULTS.retry,
) -> Publication:
    """Publish a run directory under `<destination>/<group>/<run id>/`.

    The manifest is uploaded after every other file, and each upload is retried according to
    `policy`. The function does not raise. Any error is reported through the returned
    `Publication`.

    Args:
        run: The finished run directory to publish.
        destination: A destination URI, or an already constructed sink.
        group: The archive group to publish under.
        policy: The retry policy applied to each file upload.

    Returns:
        A publication whose status is published, skipped (with the reason the sink is
        unavailable) or failed (with the redacted error).
    """
    uri = destination if isinstance(destination, str) else destination.uri
    try:
        sink = sink_for(destination) if isinstance(destination, str) else destination
        reason = sink.unavailable_reason()
        if reason:
            log.warning("publication skipped: %s; results stay local", reason)
            return Publication(PublicationStatus.SKIPPED, uri, reason=reason)
        base = f"{group}/{run.run_id}"
        for rel in run.write_manifest():  # The manifest is the last entry.
            retry.call(
                partial(sink.put, run.file(rel), f"{base}/{rel}"),
                what=f"upload {rel}",
                policy=policy,
            )
        location = sink.location(base) + "/"
        log.info("published to %s", location)
        return Publication(PublicationStatus.PUBLISHED, uri, location)
    except Exception as exc:  # noqa: BLE001
        # Any delivery error means the run is not published.
        reason = REDACTOR.scrub(str(exc))
        log.error("publication failed: %s", reason)
        return Publication(PublicationStatus.FAILED, uri, reason=reason)


@dataclass
class Archive:
    """Read access to the runs published under one destination.

    Attributes:
        sink: The sink the runs were published to.
    """

    sink: Sink

    @classmethod
    def at(cls, uri: str) -> Archive:
        """Create an archive for a destination URI.

        Args:
            uri: The destination URI the runs were published to.

        Returns:
            An archive reading from the sink for that URI.
        """
        return cls(sink_for(uri))

    def runs(self, group: str) -> list[str]:
        """Return the ids of the complete runs in a group, oldest first.

        A run without a manifest is either still uploading or was interrupted during upload,
        and is excluded so that it cannot be used as a baseline.

        Args:
            group: The archive group to list.

        Returns:
            The sorted run ids that have a manifest.
        """
        keys = set(self.sink.list(group))
        runs = {k.split("/")[1] for k in keys if k.count("/") >= 2}
        return sorted(r for r in runs if f"{group}/{r}/{RunFile.MANIFEST}" in keys)

    def results(self, group: str, run_id: str) -> list[ResultRecord]:
        """Return the result records of a published run.

        Args:
            group: The archive group of the run.
            run_id: The run identifier.

        Returns:
            The records parsed from the run's results file, skipping blank lines.
        """
        text = self.sink.get(f"{group}/{run_id}/{RunFile.RESULTS}").decode("utf-8")
        lines = [line for line in text.splitlines() if line.strip()]
        return [ResultRecord.from_dict(json.loads(line)) for line in lines]

    def outcome(self, group: str, run_id: str) -> RunOutcome:
        """Return the outcome of a published run.

        Args:
            group: The archive group of the run.
            run_id: The run identifier.

        Returns:
            The outcome parsed from the run's outcome file.
        """
        return RunOutcome.from_dict(
            json.loads(self.sink.get(f"{group}/{run_id}/{RunFile.OUTCOME}"))
        )

    def baselines(
        self,
        group: str,
        *,
        exclude: str = "",
        window: int = 1,
        label: str | None = None,
        run_id: str | None = None,
    ) -> list[tuple[str, list[ResultRecord]]]:
        """Return the runs to compare against, oldest first, with their results.

        When `run_id` is given, only that run is selected. Otherwise the latest `window` runs
        are selected, restricted to runs carrying `label` when one is given, so that ad-hoc
        runs can be kept out of the reference set.

        Args:
            group: The archive group to search.
            exclude: A run id to leave out, usually the current run.
            window: The number of most recent runs to select. Zero or less selects none.
            label: An optional `KEY=VALUE` label the runs must carry, for example
                `trigger=nightly`.
            run_id: An explicit run id to select instead of the latest runs.

        Returns:
            Pairs of run id and result records.
        """
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
        """Return whether a published run carries a label with the given value.

        A run whose outcome cannot be read is treated as not carrying the label.

        Args:
            group: The archive group of the run.
            run_id: The run identifier.
            key: The label key.
            value: The expected label value.

        Returns:
            True if the run's outcome has the label set to `value`.
        """
        try:
            return self.outcome(group, run_id).labels.get(key) == value
        except Exception:  # noqa: BLE001
            # An unreadable run is not used as a baseline.
            return False
