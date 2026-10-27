"""Read-only adapter for the kubectl and oc command-line clients.

Every cluster call goes through `KubeContext._run`, which refuses any verb outside
`KubeContext.READ_VERBS`. As a result, the adapter provides no means of running `apply`,
`delete`, `scale` or `exec`. A test tool that could modify the system it measures would not
produce trustworthy results.

An application that needs to write to a cluster, for example to deploy resources, should add a
separate adapter rather than extend this allowlist, so that the read-only guarantee continues to
hold for every tool that uses this one.

The adapter does not start or supervise port-forwards. It can print the command for the operator
to run, and `preflight.reachable` can check whether the forward is in place. A supervised
background tunnel could leak or fail without notice.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from .defaults import DEFAULTS
from .log import get_logger

log = get_logger("kube")


class KubeError(Exception):
    """Error raised when a cluster command is refused or fails."""


@dataclass(frozen=True)
class KubeContext:
    """Connection settings for reading from one cluster.

    Every field is optional. Credentials default to the client's own configuration, including a
    mounted ServiceAccount when running in-cluster. When `cli` is given, no other executable is
    tried, because a different executable could address a different cluster.

    Attributes:
        READ_VERBS: The verbs this adapter permits. All of them leave the cluster unchanged.
        kubeconfig: Path to a kubeconfig file, or None to use the client's default.
        context: Kubeconfig context name, or None to use the current context.
        namespace: Namespace for namespaced commands, or None to use the client's default.
        cli: Name of the client executable, or None to try `kubectl` and then `oc`.
        binary: Resolved path of the client executable, or None when none was found.
    """

    READ_VERBS: ClassVar[frozenset[str]] = frozenset({"get", "version", "config", "logs", "auth"})

    kubeconfig: Path | None = None
    context: str | None = None
    namespace: str | None = None
    cli: str | None = None
    binary: str | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        """Resolve the client executable from `PATH`."""
        candidates = (self.cli,) if self.cli else ("kubectl", "oc")
        found = next((p for name in candidates if (p := shutil.which(name))), None)
        object.__setattr__(self, "binary", found)

    @property
    def available(self) -> bool:
        """Whether a client executable was found."""
        return self.binary is not None

    def _run(self, verb: str, *rest: str, timeout: float = DEFAULTS.kube_timeout_s) -> str:
        """Run a read-only client command and return its standard output.

        Args:
            verb: The client verb. Must be one of `READ_VERBS`. For `auth`, only `auth can-i`
                is permitted.
            *rest: Further arguments to the verb.
            timeout: Timeout in seconds.

        Returns:
            The command's standard output.

        Raises:
            KubeError: If the verb is not permitted, no client is available, the command times
                out, or it exits with a non-zero status.
        """
        if verb not in self.READ_VERBS:
            raise KubeError(
                f"refusing verb {verb!r}: read-only (permitted: {sorted(self.READ_VERBS)})"
            )
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
            # The binary is resolved from PATH and the verb is allowlisted above.
            done = subprocess.run(  # noqa: S603
                args, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise KubeError(f"timed out after {timeout}s: {verb}") from exc
        if done.returncode != 0:
            raise KubeError((done.stderr or done.stdout or "failed").strip())
        return done.stdout

    def get_json(self, kind: str, *, selector: str | None = None, name: str | None = None) -> Any:
        """Get resources of one kind and return the parsed JSON output.

        Args:
            kind: Resource kind, such as `pods`.
            selector: Optional label selector.
            name: Optional resource name.

        Returns:
            The decoded JSON document.

        Raises:
            KubeError: If the command fails.
        """
        rest = [kind] + ([name] if name else []) + (["-l", selector] if selector else [])
        return json.loads(self._run("get", *rest, "-o", "json"))

    def pods(self, selector: str) -> list[str]:
        """Return the names of Running pods that match a label selector.

        Profiles identify pods by selector rather than by name, because pod names change each
        time a pod is rescheduled.

        Args:
            selector: Label selector.

        Returns:
            Names of the matching pods in phase `Running`.

        Raises:
            KubeError: If the command fails.
        """
        items = self.get_json("pods", selector=selector).get("items", [])
        return [
            i["metadata"]["name"] for i in items if i.get("status", {}).get("phase") == "Running"
        ]

    def logs(self, pod: str, *, container: str | None = None, since: str | None = None) -> str:
        """Return the logs of a pod.

        Args:
            pod: Pod name.
            container: Optional container name.
            since: Optional relative duration, such as `10m`, passed to `--since`.

        Returns:
            The log output.

        Raises:
            KubeError: If the command fails.
        """
        rest = (
            [pod]
            + (["-c", container] if container else [])
            + ([f"--since={since}"] if since else [])
        )
        return self._run("logs", *rest, timeout=60)

    def can_i(self, verb: str, resource: str) -> bool:
        """Return whether the current credentials permit a verb on a resource.

        Args:
            verb: Verb to check, such as `get`.
            resource: Resource to check, such as `pods`.

        Returns:
            True if the cluster answers `yes`, and False otherwise or if the command fails.
        """
        try:
            return self._run("auth", "can-i", verb, resource).strip() == "yes"
        except KubeError:
            return False

    def port_forward_command(self, target: str, ports: str) -> str:
        """Return the port-forward command for the operator to run.

        The command is returned as text only and is not executed.

        Args:
            target: Resource to forward to, such as `svc/name`.
            ports: Port mapping, such as `8000:80`.

        Returns:
            The command line.
        """
        parts = [Path(self.binary or "kubectl").name]
        if self.context:
            parts += ["--context", self.context]
        if self.namespace:
            parts += ["-n", self.namespace]
        return " ".join([*parts, "port-forward", target, ports])
