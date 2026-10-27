"""Preflight: separate "your environment is not ready" from "the target is broken".

Preflight runs before anything is tested and exits 3 when a problem would make every result
meaningless: a missing credential, a dead tunnel, an unwritable results destination. A
connection refused against localhost is almost never a target defect; reported as a test
failure it teaches people to distrust the suite.

A check is any callable returning `Problem`s. `kind` names whose fix it is; a `WARNING` limits
the evidence rather than the cases, so the run goes on.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping

from .log import get_logger
from .model import Problem, Severity
from .notifiers import NotifierError, parse_spec
from .sinks import UnsupportedDestination, sink_for

log = get_logger("preflight")

Check = Callable[[], Iterable[Problem]]


def run(checks: Iterable[Check]) -> list[Problem]:
    """Run every check (one failing does not hide the next) and log what each found."""
    problems: list[Problem] = []
    for check in checks:
        try:
            found = list(check())
        except Exception as exc:  # noqa: BLE001 -- a crashing check is itself a problem
            found = [Problem("environment", f"{getattr(check, '__name__', check)}: {exc}")]
        for p in found:
            (log.error if p.severity is Severity.ERROR else log.warning)(
                "preflight %s: %s", p.kind, p.message
            )
        problems.extend(found)
    return problems


def blocking(problems: Iterable[Problem]) -> list[Problem]:
    return [p for p in problems if p.severity is Severity.ERROR]


def named(name: str, check: Check) -> Check:
    check.__name__ = name  # type: ignore[attr-defined]
    return check


# ------------------------------------------------------------------ ready-made checks


def env_set(
    *names: str, kind: str = "credentials", environ: Mapping[str, str] | None = None
) -> Check:
    env = environ if environ is not None else os.environ
    return named(
        f"env_set({', '.join(names)})",
        lambda: [Problem(kind, f"{n} is not set") for n in names if not env.get(n)],
    )


def reachable(name: str, url: str, *, timeout: float = 5.0, ok_status: Iterable[int] = ()) -> Check:
    """Something answers at `url`. Any HTTP status counts unless `ok_status` is given; a refused
    or timed-out connection is the operator's environment, not the target."""
    wanted = set(ok_status)

    def check() -> list[Problem]:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            return [Problem("reachability", f"{name}: nothing answers at {url}: {reason}")]
        if wanted and status not in wanted:
            return [
                Problem("reachability", f"{name}: {url} answered {status}, not {sorted(wanted)}")
            ]
        return []

    return named(f"reachable({name})", check)


def destination_writable(uri: str | None) -> Check:
    """The results destination resolves, has what it needs, and accepts a write."""

    def check() -> list[Problem]:
        if not uri:
            return [
                Problem("output", "no results destination; results stay local", Severity.WARNING)
            ]
        try:
            sink = sink_for(uri)
        except UnsupportedDestination as exc:
            return [Problem("output", str(exc))]
        reason = sink.unavailable_reason()
        if reason:
            return [Problem("output", f"{uri}: {reason}; results stay local", Severity.WARNING)]
        try:
            sink.write_check()
        except Exception as exc:  # noqa: BLE001
            return [Problem("output", f"{uri} is not writable: {exc}")]
        return []

    return named("destination_writable", check)


def notifiers_valid(specs: Iterable[str]) -> Check:
    """Every notifier spec parses and its variables are set, before the run rather than after."""

    def check() -> list[Problem]:
        problems = []
        for spec in specs:
            for one in (s for s in spec.split(",") if s.strip()):
                try:
                    parse_spec(one)
                except NotifierError as exc:
                    problems.append(Problem("notification", f"{one.split(':', 1)[0]}: {exc}"))
        return problems

    return named("notifiers_valid", check)
