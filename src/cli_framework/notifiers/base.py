"""The notifier protocol, the registry of kinds, and delivery to several at once."""

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
    """A notifier could not deliver, or its spec is invalid."""


class Notifier(Protocol):
    name: str

    def send(self, report: RunReport) -> None:
        """Deliver the report, retrying within bounds; raise on final failure."""
        ...


NotifierFactory = Callable[[str], Notifier]  # target string -> notifier


def resolve_target(target: str, environ: Mapping[str, str] | None = None) -> str:
    """`env:NAME` reads the variable (registered as a secret); anything else is literal."""
    if target.startswith("env:"):
        name = target[4:]
        try:
            return REDACTOR.secret_from_env(name, environ=dict(environ) if environ else None) or ""
        except KeyError:
            raise NotifierError(f"{name} is not set") from None
    return target


def parse_spec(spec: str) -> Notifier:
    """`<kind>:<target>` to a notifier. The target may be empty (`log:`)."""
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
    """Several specs; also accepts comma-separated lists (as from one environment variable)."""
    out = []
    for spec in specs:
        out += [parse_spec(s) for s in spec.split(",") if s.strip()]
    return out


@dataclass(frozen=True)
class Delivery:
    notifier: str
    ok: bool
    error: str | None = None


def notify_all(notifiers: Sequence[Notifier], report: RunReport) -> list[Delivery]:
    """Send to every notifier; never raises. One channel failing does not stop the others."""
    results = []
    for notifier in notifiers:
        try:
            notifier.send(report)
            results.append(Delivery(notifier.name, True))
        except Exception as exc:  # noqa: BLE001 -- reported, and turned into exit 5 upstream
            error = REDACTOR.scrub(str(exc))
            log.error("notification via %s failed: %s", notifier.name, error)
            results.append(Delivery(notifier.name, False, error))
    return results
