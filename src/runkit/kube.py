"""A read-only kubectl/oc adapter: a structural guarantee, not a convention.

Every cluster call goes through `_run`, which refuses any verb outside `READ_VERBS`. There is
no code path from the tool to `apply`, `delete`, `scale` or `exec`. A test tool that can change
what it measures cannot be trusted with a kubeconfig.

An app that genuinely needs to write (it deploys things) should not widen this allowlist; it
should add a separate, explicit adapter, so the read-only guarantee stays true for every tool
that uses this one.

It also never starts or supervises a port-forward: it can print the command and check whether
it is satisfied (see `preflight.reachable`), but a supervised background tunnel is one more
thing to leak or die silently.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .log import get_logger

log = get_logger("kube")

READ_VERBS = frozenset({"get", "version", "config", "logs", "auth"})
DEFAULT_TIMEOUT = 20.0


class KubeError(Exception):
    pass


@dataclass(frozen=True)
class KubeContext:
    """Which cluster to read. Every field is optional: credentials default to the CLI's own
    configuration, including a mounted ServiceAccount in-cluster. An explicit `cli` never
    falls back to another executable, which could address a different cluster."""

    kubeconfig: Path | None = None
    context: str | None = None
    namespace: str | None = None
    cli: str | None = None
    binary: str | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        candidates = (self.cli,) if self.cli else ("kubectl", "oc")
        found = next((p for name in candidates if (p := shutil.which(name))), None)
        object.__setattr__(self, "binary", found)

    @property
    def available(self) -> bool:
        return self.binary is not None

    def _run(self, verb: str, *rest: str, timeout: float = DEFAULT_TIMEOUT) -> str:
        if verb not in READ_VERBS:
            raise KubeError(f"refusing verb {verb!r}: read-only (permitted: {sorted(READ_VERBS)})")
        if verb == "auth" and rest[:1] != ("can-i",):
            raise KubeError("only `auth can-i` is permitted")
        if not self.binary:
            raise KubeError(f"no cluster CLI found ({self.cli or 'kubectl, oc'})")
        args = [self.binary]
        if self.kubeconfig:
            args += ["--kubeconfig", str(self.kubeconfig)]
        if self.context:
            args += ["--context", self.context]
        if self.namespace and verb in ("get", "logs", "auth"):
            args += ["-n", self.namespace]
        args += [verb, *rest]
        log.debug("cluster: %s", " ".join(args))
        try:
            done = subprocess.run(  # noqa: S603 -- resolved binary, allowlisted verb
                args, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise KubeError(f"timed out after {timeout}s: {verb}") from exc
        if done.returncode != 0:
            raise KubeError((done.stderr or done.stdout or "failed").strip())
        return done.stdout

    # --- reads

    def get_json(self, kind: str, *, selector: str | None = None, name: str | None = None) -> Any:
        rest = [kind] + ([name] if name else []) + (["-l", selector] if selector else [])
        return json.loads(self._run("get", *rest, "-o", "json"))

    def pods(self, selector: str) -> list[str]:
        """Names of Running pods matching a selector: profiles name selectors, never pods,
        because pod names change on every reschedule."""
        items = self.get_json("pods", selector=selector).get("items", [])
        return [
            i["metadata"]["name"] for i in items if i.get("status", {}).get("phase") == "Running"
        ]

    def logs(self, pod: str, *, container: str | None = None, since: str | None = None) -> str:
        rest = (
            [pod]
            + (["-c", container] if container else [])
            + ([f"--since={since}"] if since else [])
        )
        return self._run("logs", *rest, timeout=60)

    def can_i(self, verb: str, resource: str) -> bool:
        try:
            return self._run("auth", "can-i", verb, resource).strip() == "yes"
        except KubeError:
            return False

    def port_forward_command(self, target: str, ports: str) -> str:
        """The command for the operator to run; never run here."""
        parts = [Path(self.binary or "kubectl").name]
        if self.context:
            parts += ["--context", self.context]
        if self.namespace:
            parts += ["-n", self.namespace]
        return " ".join([*parts, "port-forward", target, ports])
