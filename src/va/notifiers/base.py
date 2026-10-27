"""The notifier protocol, notifier spec parsing, and delivery to several notifiers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from ..log import get_logger
from ..plugins import PLUGINS, UnknownPlugin
from ..redact import REDACTOR
from ..report import RunReport

log = get_logger("notify")


class NotifierError(Exception):
    """Raised when a notifier cannot deliver a report or its spec is invalid."""


class Notifier(Protocol):
    """A channel that delivers a run report.

    Attributes:
        name: A short name for the channel, used in logs and delivery results.
    """

    name: str

    def send(self, report: RunReport) -> None:
        """Deliver a report, retrying within bounds, and raise if delivery finally fails.

        Args:
            report: The run report to deliver.
        """
        ...


NotifierFactory = Callable[[str], Notifier]  # Maps a target string to a notifier.


def resolve_target(target: str, environ: Mapping[str, str] | None = None) -> str:
    """Resolve a notifier target, reading `env:NAME` targets from the environment.

    A value read from the environment is registered with the redactor as a secret. Any other
    target is returned unchanged.

    Args:
        target: The target part of a notifier spec.
        environ: An optional environment mapping to read from instead of `os.environ`.

    Returns:
        The resolved target.

    Raises:
        NotifierError: If an `env:NAME` target names a variable that is not set.
    """
    if target.startswith("env:"):
        name = target[4:]
        try:
            return REDACTOR.secret_from_env(name, environ=dict(environ) if environ else None) or ""
        except KeyError:
            raise NotifierError(f"{name} is not set") from None
    return target


def parse_spec(spec: str) -> Notifier:
    """Create a notifier from a `<kind>:<target>` spec.

    The target may be empty, as in `log:`.

    Args:
        spec: The notifier spec.

    Returns:
        The notifier built by the factory registered for the kind.

    Raises:
        NotifierError: If the spec is malformed, the kind is unknown, or the target cannot be
            resolved.
    """
    kind, sep, target = spec.strip().partition(":")
    if not sep or not kind:
        raise NotifierError(
            f"a notifier is <kind>:<target>, e.g. slack:env:SLACK_WEBHOOK, not {spec!r}"
        )
    try:
        factory = PLUGINS.notifiers.get(kind)
    except UnknownPlugin as exc:
        raise NotifierError(str(exc)) from None
    return factory(resolve_target(target))


def parse_specs(specs: Iterable[str]) -> list[Notifier]:
    """Create notifiers from several specs.

    Each item may itself be a comma-separated list, as when the specs come from a single
    environment variable. Empty entries are ignored.

    Args:
        specs: The notifier specs.

    Returns:
        The notifiers, in the order given.
    """
    out = []
    for spec in specs:
        out += [parse_spec(s) for s in spec.split(",") if s.strip()]
    return out


@dataclass(frozen=True)
class Delivery:
    """The result of delivering a report through one notifier.

    Attributes:
        notifier: The notifier's name.
        ok: Whether delivery succeeded.
        error: The redacted error message when delivery failed, otherwise None.
    """

    notifier: str
    ok: bool
    error: str | None = None


def notify_all(notifiers: Sequence[Notifier], report: RunReport) -> list[Delivery]:
    """Send a report through every notifier and collect the results.

    The function does not raise. A failure in one notifier is logged and recorded, and does
    not prevent delivery through the others.

    Args:
        notifiers: The notifiers to deliver through.
        report: The run report to deliver.

    Returns:
        One delivery result per notifier, in order.
    """
    results = []
    for notifier in notifiers:
        try:
            notifier.send(report)
            results.append(Delivery(notifier.name, True))
        except Exception as exc:  # noqa: BLE001
            # The error is reported, and the caller maps it to exit code 5.
            error = REDACTOR.scrub(str(exc))
            log.error("notification via %s failed: %s", notifier.name, error)
            results.append(Delivery(notifier.name, False, error))
    return results
