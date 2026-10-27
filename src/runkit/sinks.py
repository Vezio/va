"""Where run directories go: storage backends, selected by URI scheme.

Built in: `file://` and `s3://` (boto3, the `s3` extra; any S3-compatible store through
AWS_ENDPOINT_URL_S3 or AWS_ENDPOINT_URL). Add another (GCS, Azure Blob, an HTTP upload API)
by implementing `Sink` and registering a factory for its scheme:

    @SINKS.register("gs")
    def gcs(uri: str) -> Sink: ...

or from another package, through the `runkit.sinks` entry point group.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from .plugins import PluginRegistry, UnknownPlugin
from .redact import REDACTOR

WRITE_CHECK = ".write-check"


class UnsupportedDestination(ValueError):
    pass


class Sink(Protocol):
    """A key-value store of files. Keys are relative POSIX paths."""

    uri: str

    def unavailable_reason(self) -> str | None:
        """Why this sink cannot be used here (no credentials, no library), or None. Checked
        before a run, so a missing credential skips publication instead of failing it."""
        ...

    def write_check(self) -> None:
        """Prove a write works, before an hour-long run rather than after."""
        ...

    def put(self, local: Path, key: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def list(self, prefix: str) -> list[str]:
        """Every key under `prefix`, sorted."""
        ...

    def location(self, key: str) -> str:
        """A URI a person can follow to `key`."""
        ...


SinkFactory = Callable[[str], Sink]  # uri -> sink
SINKS: PluginRegistry[SinkFactory] = PluginRegistry("result sink", "runkit.sinks")


@dataclass
class FileSink:
    uri: str
    root: Path = field(init=False)

    def __post_init__(self) -> None:
        self.root = Path(urlparse(self.uri).path).expanduser()

    def unavailable_reason(self) -> str | None:
        return None

    def write_check(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / WRITE_CHECK).write_text("ok\n", encoding="utf-8")

    def put(self, local: Path, key: str) -> None:
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local, target)

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def list(self, prefix: str) -> list[str]:
        base = self.root / prefix
        if not base.is_dir():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file())

    def location(self, key: str) -> str:
        return str(self.root / key)


@dataclass
class S3Sink:
    uri: str
    bucket: str = field(init=False)
    prefix: str = field(init=False)
    _client: Any = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        parsed = urlparse(self.uri)
        if not parsed.netloc:
            raise UnsupportedDestination(f"s3:// needs a bucket: {self.uri!r}")
        self.bucket, self.prefix = parsed.netloc, parsed.path.strip("/")

    def _key(self, key: str) -> str:
        return "/".join(p for p in (self.prefix, key) if p)

    def unavailable_reason(self) -> str | None:
        try:
            import boto3  # noqa: F401, PLC0415 -- optional, the s3 extra
        except ImportError:
            return "s3:// needs boto3: install the s3 extra"
        env = os.environ
        has_keys = env.get("AWS_ACCESS_KEY_ID") and env.get("AWS_SECRET_ACCESS_KEY")
        if not (
            has_keys
            or env.get("AWS_PROFILE")
            or env.get("AWS_ROLE_ARN")
            or env.get("AWS_WEB_IDENTITY_TOKEN_FILE")
        ):
            return "no AWS credentials in the environment"
        return None

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3  # noqa: PLC0415

            # An S3 error can echo the access key id into a log or outcome.json.
            REDACTOR.add(
                os.environ.get("AWS_ACCESS_KEY_ID"), os.environ.get("AWS_SECRET_ACCESS_KEY")
            )
            endpoint = os.environ.get("AWS_ENDPOINT_URL_S3") or os.environ.get("AWS_ENDPOINT_URL")
            self._client = boto3.client("s3", endpoint_url=endpoint or None)
        return self._client

    def write_check(self) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._key(WRITE_CHECK), Body=b"ok\n")

    def put(self, local: Path, key: str) -> None:
        self.client.upload_file(str(local), self.bucket, self._key(key))

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()

    def list(self, prefix: str) -> list[str]:
        base = self._key(prefix).rstrip("/") + "/"
        strip = len(self.prefix) + 1 if self.prefix else 0
        keys: list[str] = []
        for page in self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=base
        ):
            keys += [o["Key"][strip:] for o in page.get("Contents", [])]
        return sorted(keys)

    def location(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._key(key)}"


SINKS.register("file", FileSink)
SINKS.register("s3", S3Sink)


def sink_for(uri: str) -> Sink:
    scheme = urlparse(uri).scheme
    if not scheme:
        raise UnsupportedDestination(f"a destination is a URI such as file:///path, not {uri!r}")
    try:
        return SINKS.get(scheme)(uri)
    except UnknownPlugin as exc:
        raise UnsupportedDestination(str(exc)) from None
