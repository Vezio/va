"""Storage backends for run directories, selected by URI scheme.

Two schemes are built in. `file://` writes to the local file system. `s3://` uses boto3 (the
`s3` extra) and supports any S3-compatible store through AWS_ENDPOINT_URL_S3 or
AWS_ENDPOINT_URL; such stores are addressed path-style unless AWS_S3_ADDRESSING_STYLE
specifies otherwise. Another backend (GCS, Azure Blob, an HTTP upload API) is added by
implementing `Sink` and registering a factory for its scheme:

    @PLUGINS.sinks.register("gs")
    def gcs(uri: str) -> Sink: ...

A separate package can register a factory through the `va.sinks` entry point group instead.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from .layout import ArchiveKey
from .plugins import PLUGINS, UnknownPlugin
from .redact import REDACTOR


class UnsupportedDestination(ValueError):
    """Raised when a destination URI is malformed or names an unregistered scheme."""


class Sink(Protocol):
    """A key-value store of files, addressed by relative POSIX paths.

    Attributes:
        uri: The destination URI the sink was created from.
    """

    uri: str

    def unavailable_reason(self) -> str | None:
        """Return why the sink cannot be used in this environment, or None if it can.

        The check runs before a run starts, so that a missing credential or library causes
        publication to be skipped rather than failed.

        Returns:
            A short human-readable reason, or None when the sink is usable.
        """
        ...

    def write_check(self) -> None:
        """Write a small marker file to confirm that writes succeed.

        This is called before a run so that a permissions problem is found before the run
        rather than after it.
        """
        ...

    def put(self, local: Path, key: str) -> None:
        """Upload a local file under a key.

        Args:
            local: Path of the file to upload.
            key: Relative POSIX path to store the file under.
        """
        ...

    def get(self, key: str) -> bytes:
        """Return the contents stored under a key.

        Args:
            key: Relative POSIX path of the stored file.

        Returns:
            The file contents.
        """
        ...

    def list(self, prefix: str) -> list[str]:
        """Return every key under a prefix, sorted.

        Args:
            prefix: Relative POSIX path to list beneath.

        Returns:
            The sorted keys, relative to the sink root.
        """
        ...

    def location(self, key: str) -> str:
        """Return a URI or path a person can follow to reach a key.

        Args:
            key: Relative POSIX path of the stored file or directory.

        Returns:
            A location string suitable for display.
        """
        ...


SinkFactory = Callable[[str], Sink]  # Maps a destination URI to a sink.


@dataclass
class FileSink:
    """A sink that stores files in a local directory.

    Attributes:
        uri: The `file://` URI the sink was created from.
        root: The local directory derived from the URI path, with `~` expanded.
    """

    uri: str
    root: Path = field(init=False)

    def __post_init__(self) -> None:
        """Derive the root directory from the URI."""
        self.root = Path(urlparse(self.uri).path).expanduser()

    def unavailable_reason(self) -> str | None:
        """Return None, because a local directory needs no credentials or optional libraries."""
        return None

    def write_check(self) -> None:
        """Create the root directory and write the write-check marker into it."""
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ArchiveKey.WRITE_CHECK).write_text("ok\n", encoding="utf-8")

    def put(self, local: Path, key: str) -> None:
        """Copy a local file to the key's path under the root, creating directories.

        Args:
            local: Path of the file to copy.
            key: Relative POSIX path to store the file under.
        """
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local, target)

    def get(self, key: str) -> bytes:
        """Return the contents of the file stored under a key.

        Args:
            key: Relative POSIX path of the stored file.

        Returns:
            The file contents.
        """
        return (self.root / key).read_bytes()

    def list(self, prefix: str) -> list[str]:
        """Return every file key under a prefix, sorted.

        Args:
            prefix: Relative POSIX path to list beneath.

        Returns:
            The sorted keys, or an empty list if the prefix is not a directory.
        """
        base = self.root / prefix
        if not base.is_dir():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file())

    def location(self, key: str) -> str:
        """Return the local path of a key.

        Args:
            key: Relative POSIX path of the stored file or directory.

        Returns:
            The absolute or user-relative local path as a string.
        """
        return str(self.root / key)


@dataclass
class S3Sink:
    """A sink that stores files in an S3 bucket or an S3-compatible store.

    Attributes:
        uri: The `s3://bucket/optional/prefix` URI the sink was created from.
        bucket: The bucket name taken from the URI.
        prefix: The key prefix taken from the URI path, without leading or trailing slashes.

    Raises:
        UnsupportedDestination: If the URI does not name a bucket.
    """

    uri: str
    bucket: str = field(init=False)
    prefix: str = field(init=False)
    _client: Any = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        """Split the URI into bucket and prefix.

        Raises:
            UnsupportedDestination: If the URI does not name a bucket.
        """
        parsed = urlparse(self.uri)
        if not parsed.netloc:
            raise UnsupportedDestination(f"s3:// needs a bucket: {self.uri!r}")
        self.bucket, self.prefix = parsed.netloc, parsed.path.strip("/")

    def _key(self, key: str) -> str:
        """Return the full object key for a sink-relative key.

        Args:
            key: Relative POSIX path within the sink.

        Returns:
            The key joined to the sink prefix.
        """
        return "/".join(p for p in (self.prefix, key) if p)

    def unavailable_reason(self) -> str | None:
        """Return why S3 cannot be used, or None if boto3 and credentials are present.

        Credentials are considered present when the environment provides an access key pair,
        a profile name, a role ARN or a web identity token file.

        Returns:
            A short reason, or None when the sink is usable.
        """
        try:
            # boto3 is optional and is provided by the s3 extra.
            import boto3  # noqa: F401, PLC0415
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
        """The boto3 S3 client, created on first use.

        Creating the client registers the AWS key pair with the redactor and selects the
        addressing style from the environment.
        """
        if self._client is None:
            import boto3  # noqa: PLC0415

            # An S3 error message can include the access key id, which could then reach a log
            # or outcome.json.
            REDACTOR.add(
                os.environ.get("AWS_ACCESS_KEY_ID"), os.environ.get("AWS_SECRET_ACCESS_KEY")
            )
            from botocore.config import Config  # noqa: PLC0415

            endpoint = os.environ.get("AWS_ENDPOINT_URL_S3") or os.environ.get("AWS_ENDPOINT_URL")
            # S3-compatible stores generally require path-style addressing (bucket in the path
            # rather than the hostname), while AWS accepts either. AWS_S3_ADDRESSING_STYLE
            # overrides the choice.
            style = os.environ.get("AWS_S3_ADDRESSING_STYLE") or ("path" if endpoint else "auto")
            self._client = boto3.client(
                "s3", endpoint_url=endpoint or None, config=Config(s3={"addressing_style": style})
            )
        return self._client

    def write_check(self) -> None:
        """Write the write-check marker object under the sink prefix."""
        self.client.put_object(
            Bucket=self.bucket, Key=self._key(ArchiveKey.WRITE_CHECK), Body=b"ok\n"
        )

    def put(self, local: Path, key: str) -> None:
        """Upload a local file to the bucket.

        Args:
            local: Path of the file to upload.
            key: Relative POSIX path to store the file under.
        """
        self.client.upload_file(str(local), self.bucket, self._key(key))

    def get(self, key: str) -> bytes:
        """Return the contents of the object stored under a key.

        Args:
            key: Relative POSIX path of the stored object.

        Returns:
            The object body.
        """
        return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()

    def list(self, prefix: str) -> list[str]:
        """Return every key under a prefix, relative to the sink prefix and sorted.

        Args:
            prefix: Relative POSIX path to list beneath.

        Returns:
            The sorted keys, with the sink prefix removed.
        """
        base = self._key(prefix).rstrip("/") + "/"
        strip = len(self.prefix) + 1 if self.prefix else 0
        keys: list[str] = []
        for page in self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=base
        ):
            keys += [o["Key"][strip:] for o in page.get("Contents", [])]
        return sorted(keys)

    def location(self, key: str) -> str:
        """Return the `s3://` URI of a key.

        Args:
            key: Relative POSIX path of the stored object or prefix.

        Returns:
            The full `s3://bucket/key` URI.
        """
        return f"s3://{self.bucket}/{self._key(key)}"


PLUGINS.sinks.register("file", FileSink)
PLUGINS.sinks.register("s3", S3Sink)


def sink_for(uri: str) -> Sink:
    """Create the sink registered for a destination URI's scheme.

    Args:
        uri: A destination URI such as `file:///path` or `s3://bucket/prefix`.

    Returns:
        A sink for the URI.

    Raises:
        UnsupportedDestination: If the URI has no scheme or no sink is registered for it.
    """
    scheme = urlparse(uri).scheme
    if not scheme:
        raise UnsupportedDestination(f"a destination is a URI such as file:///path, not {uri!r}")
    try:
        return PLUGINS.sinks.get(scheme)(uri)
    except UnknownPlugin as exc:
        raise UnsupportedDestination(str(exc)) from None
