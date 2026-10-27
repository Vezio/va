"""The run directory: one place for every file name, writers that always redact.

A run leaves `<work dir>/<run id>/` behind, locally, always. Publishing is a separate step over
the finished directory, and `MANIFEST.json` is written last, so a reader can tell a complete
run from a partial one by the manifest alone.
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

from .model import ResultRecord, RunOutcome, to_jsonable
from .redact import REDACTOR, Redactor

RESULTS = "results.jsonl"  # one ResultRecord per line
OUTCOME = "outcome.json"  # the RunOutcome
LOG = "run.log"  # the DEBUG log of the run, redacted
SUMMARY = "summary.md"  # a human summary (also fit for $GITHUB_STEP_SUMMARY)
COMPARISON = "comparison.json"  # against earlier published runs
COMPARISON_MD = "comparison.md"
ENV = "env.json"  # tool version and the choices made at start
EVIDENCE = "evidence"  # evidence/<target>/<case>[<variant>]/...
MANIFEST = "MANIFEST.json"  # every other file hashed; written and published last

MANIFEST_SCHEMA = "runkit.manifest/1"


def new_run_id() -> str:
    """Sortable, unique, and safe as a path segment and in a Kubernetes name."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:4]}"


@dataclass(frozen=True)
class RunDir:
    path: Path
    redactor: Redactor = REDACTOR
    # Cases running concurrently append to shared files (exchanges.jsonl): writes serialise.
    _lock: threading.Lock = field(default_factory=threading.Lock, compare=False, repr=False)

    @classmethod
    def create(cls, work_dir: Path, run_id: str | None = None) -> RunDir:
        path = (work_dir / (run_id or new_run_id())).resolve()
        path.mkdir(parents=True, exist_ok=False)
        return cls(path)

    @classmethod
    def at(cls, path: Path) -> RunDir:
        """An existing run directory, or a new one at exactly this path."""
        path = path.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return cls(path)

    @property
    def run_id(self) -> str:
        return self.path.name

    def file(self, name: str) -> Path:
        return self.path / name

    # --- writers: everything is redacted on the way out

    def write_text(self, name: str, text: str) -> Path:
        target = self.path / name
        text = self.redactor.scrub(text)
        with self._lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return target

    def write_json(self, name: str, value: Any) -> Path:
        body = self.redactor.value(to_jsonable(value))
        return self.write_text(name, json.dumps(body, indent=2) + "\n")

    def append_jsonl(self, name: str, records: Iterable[Any]) -> Path:
        target = self.path / name
        lines = [
            json.dumps(self.redactor.value(to_jsonable(r)), sort_keys=True) + "\n" for r in records
        ]
        with self._lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.writelines(lines)
        return target

    # --- readers

    def read_json(self, name: str) -> Any | None:
        target = self.path / name
        return json.loads(target.read_text(encoding="utf-8")) if target.is_file() else None

    def read_jsonl(self, name: str) -> list[dict[str, Any]]:
        target = self.path / name
        if not target.is_file():
            return []
        lines = target.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    # --- the typed files

    def results(self) -> list[ResultRecord]:
        return [ResultRecord.from_dict(r) for r in self.read_jsonl(RESULTS)]

    def add_result(self, record: ResultRecord) -> None:
        self.append_jsonl(RESULTS, [record])

    def outcome(self) -> RunOutcome | None:
        body = self.read_json(OUTCOME)
        return RunOutcome.from_dict(body) if body is not None else None

    def save_outcome(self, outcome: RunOutcome) -> None:
        self.write_json(OUTCOME, outcome)

    # --- the manifest, last

    def files(self) -> list[str]:
        """Every file except the manifest, as sorted relative POSIX paths."""
        return sorted(
            p.relative_to(self.path).as_posix()
            for p in self.path.rglob("*")
            if p.is_file() and p.name != MANIFEST
        )

    def write_manifest(self) -> list[str]:
        """Hash every file into MANIFEST.json; return the files in publication order, the
        manifest last."""
        entries: list[dict[str, Any]] = [
            {"path": rel, "size": self.file(rel).stat().st_size, "sha256": _sha256(self.file(rel))}
            for rel in self.files()
        ]
        body = {"schema": MANIFEST_SCHEMA, "run_id": self.run_id, "files": entries}
        self.file(MANIFEST).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        return [str(e["path"]) for e in entries] + [MANIFEST]

    def verify_manifest(self) -> list[str]:
        """Problems with the manifest: missing, or a file whose presence or hash differs."""
        manifest = self.read_json(MANIFEST)
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
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
