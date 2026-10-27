"""Access to a run directory, with writers that redact all output.

A run always leaves `<work dir>/<run id>/` on the local file system. Publishing is a separate
step performed on the finished directory. `MANIFEST.json` is written last, so a reader can
distinguish a complete run from a partial one by the presence of the manifest.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .layout import RunFile, Schema
from .model import ResultRecord, RunOutcome, to_jsonable
from .redact import REDACTOR, Redactor


def new_run_id() -> str:
    """Return a new run identifier.

    The identifier is unique, safe to use as a path segment, and sorts by start time with
    millisecond precision, for example `2026-10-27T02-08-44-123Z-ab12`. Readers treat the last
    entry of a sorted list as the latest run, so runs started within the same second must still
    sort in start order.

    Returns:
        The run identifier.
    """
    now = datetime.now(UTC)
    stamp = f"{now:%Y-%m-%dT%H-%M-%S}-{now.microsecond // 1000:03d}Z"
    return f"{stamp}-{uuid.uuid4().hex[:4]}"


@dataclass(frozen=True)
class RunDir:
    """A run directory and the typed files inside it.

    All writers pass their output through the redactor before it reaches disk.

    Attributes:
        path: The absolute path of the run directory.
        redactor: The redactor applied to everything written.
    """

    path: Path
    redactor: Redactor = REDACTOR
    # Concurrent cases append to shared files such as exchanges.jsonl, so writes are serialised.
    _lock: threading.Lock = field(default_factory=threading.Lock, compare=False, repr=False)

    @classmethod
    def create(cls, work_dir: Path, run_id: str | None = None) -> RunDir:
        """Create a new run directory under a work directory.

        Args:
            work_dir: The parent directory for runs.
            run_id: The run identifier. A new one is generated if omitted.

        Returns:
            The new run directory.

        Raises:
            FileExistsError: If the directory already exists.
        """
        path = (work_dir / (run_id or new_run_id())).resolve()
        path.mkdir(parents=True, exist_ok=False)
        return cls(path)

    @classmethod
    def at(cls, path: Path) -> RunDir:
        """Open an existing run directory, or create one at exactly this path.

        Args:
            path: The path of the run directory.

        Returns:
            The run directory.
        """
        path = path.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return cls(path)

    @property
    def run_id(self) -> str:
        """The run identifier, which is the directory name."""
        return self.path.name

    def file(self, name: str) -> Path:
        """Return the path of a file inside the run directory.

        Args:
            name: The path relative to the run directory.

        Returns:
            The absolute path.
        """
        return self.path / name

    def write_text(self, name: str, text: str) -> Path:
        """Redact and write text to a file, creating parent directories as needed.

        Args:
            name: The path relative to the run directory.
            text: The text to write.

        Returns:
            The path of the written file.
        """
        target = self.path / name
        text = self.redactor.scrub(text)
        with self._lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return target

    def write_json(self, name: str, value: Any) -> Path:
        """Redact and write a value as indented JSON.

        Args:
            name: The path relative to the run directory.
            value: The value to write. Dataclasses and enums are converted to JSON values.

        Returns:
            The path of the written file.
        """
        body = self.redactor.value(to_jsonable(value))
        return self.write_text(name, json.dumps(body, indent=2) + "\n")

    def append_jsonl(self, name: str, records: Iterable[Any]) -> Path:
        """Redact and append records to a JSON Lines file, one record per line.

        Args:
            name: The path relative to the run directory.
            records: The records to append.

        Returns:
            The path of the file.
        """
        target = self.path / name
        lines = [
            json.dumps(self.redactor.value(to_jsonable(r)), sort_keys=True) + "\n" for r in records
        ]
        with self._lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.writelines(lines)
        return target

    def read_json(self, name: str) -> Any | None:
        """Read a JSON file.

        Args:
            name: The path relative to the run directory.

        Returns:
            The parsed value, or None if the file does not exist.
        """
        target = self.path / name
        return json.loads(target.read_text(encoding="utf-8")) if target.is_file() else None

    def read_jsonl(self, name: str) -> list[dict[str, Any]]:
        """Read a JSON Lines file, ignoring blank lines.

        Args:
            name: The path relative to the run directory.

        Returns:
            The parsed records, or an empty list if the file does not exist.
        """
        target = self.path / name
        if not target.is_file():
            return []
        lines = target.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def results(self) -> list[ResultRecord]:
        """Return the result records of the run.

        Returns:
            The records from `results.jsonl`, in the order they were written.
        """
        return [ResultRecord.from_dict(r) for r in self.read_jsonl(RunFile.RESULTS)]

    def add_result(self, record: ResultRecord) -> None:
        """Append a result record to `results.jsonl`.

        Args:
            record: The record to append.
        """
        self.append_jsonl(RunFile.RESULTS, [record])

    def outcome(self) -> RunOutcome | None:
        """Return the run outcome.

        Returns:
            The outcome from `outcome.json`, or None if it has not been written.
        """
        body = self.read_json(RunFile.OUTCOME)
        return RunOutcome.from_dict(body) if body is not None else None

    def save_outcome(self, outcome: RunOutcome) -> None:
        """Write the run outcome to `outcome.json`.

        Args:
            outcome: The outcome to write.
        """
        self.write_json(RunFile.OUTCOME, outcome)

    def files(self) -> list[str]:
        """Return all files except the manifest, as sorted relative POSIX paths.

        Returns:
            The relative paths.
        """
        return sorted(
            p.relative_to(self.path).as_posix()
            for p in self.path.rglob("*")
            if p.is_file() and p.name != RunFile.MANIFEST
        )

    def write_manifest(self) -> list[str]:
        """Write `MANIFEST.json` with the size and SHA-256 hash of every other file.

        Returns:
            The relative paths in publication order, with the manifest last.
        """
        entries: list[dict[str, Any]] = [
            {"path": rel, "size": self.file(rel).stat().st_size, "sha256": _sha256(self.file(rel))}
            for rel in self.files()
        ]
        body = {"schema": Schema.MANIFEST.value, "run_id": self.run_id, "files": entries}
        self.file(RunFile.MANIFEST).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        return [str(e["path"]) for e in entries] + [RunFile.MANIFEST.value]

    def verify_manifest(self) -> list[str]:
        """Check the run directory against its manifest.

        Returns:
            A description of each problem found: a missing manifest, a listed file that is
            missing or whose hash differs, or a file that is present but not listed. The list is
            empty if the directory matches the manifest.
        """
        manifest = self.read_json(RunFile.MANIFEST)
        if manifest is None:
            return ["no manifest: the run is incomplete"]
        listed = {e["path"]: e for e in manifest.get("files", [])}
        problems = []
        for rel, entry in listed.items():
            if not self.file(rel).is_file():
                problems.append(f"{rel}: listed but missing")
            elif _sha256(self.file(rel)) != entry["sha256"]:
                problems.append(f"{rel}: hash differs")
        problems += [f"{rel}: present but not listed" for rel in set(self.files()) - set(listed)]
        return problems


def _sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file, read in 1 MiB chunks.

    Args:
        path: The file to hash.

    Returns:
        The hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
