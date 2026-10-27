"""Preflight checks that distinguish an unready environment from a defective target.

Preflight runs before any case and causes exit code 3 when a problem would make every result
meaningless, such as a missing credential, an unavailable tunnel or an unwritable results
destination. A refused connection to localhost is rarely a defect in the target, and reporting it
as a test failure would make the results misleading.

A check is any callable that returns `Problem` instances. The problem's `kind` identifies who is
responsible for the fix. A problem of severity `WARNING` limits the evidence collected rather than
the cases that can run, so the run continues.
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
    """Run every check and log the problems each one finds.

    Each check runs regardless of the outcome of the others. A check that raises is reported as
    an `environment` problem.

    Args:
        checks: The checks to run.

    Returns:
        All problems found, in the order the checks ran.
    """
    problems: list[Problem] = []
    for check in checks:
        try:
            found = list(check())
        # A check that raises is reported as a problem rather than aborting preflight.
        except Exception as exc:  # noqa: BLE001
            found = [Problem("environment", f"{getattr(check, '__name__', check)}: {exc}")]
        for p in found:
            (log.error if p.severity is Severity.ERROR else log.warning)(
                "preflight %s: %s", p.kind, p.message
            )
        problems.extend(found)
    return problems


def blocking(problems: Iterable[Problem]) -> list[Problem]:
    """Return the problems that prevent the run from starting.

    Args:
        problems: Problems found by preflight.

    Returns:
        The problems of severity `ERROR`.
    """
    return [p for p in problems if p.severity is Severity.ERROR]


def named(name: str, check: Check) -> Check:
    """Set the name used for a check in log and problem messages.

    Args:
        name: Name to assign.
        check: The check to name.

    Returns:
        The same check, with its `__name__` set.
    """
    check.__name__ = name  # type: ignore[attr-defined]
    return check


def env_set(
    *names: str, kind: str = "credentials", environ: Mapping[str, str] | None = None
) -> Check:
    """Create a check that each named environment variable is set and non-empty.

    Args:
        *names: Names of the required environment variables.
        kind: Problem kind reported for a missing variable.
        environ: Environment to inspect. Defaults to `os.environ`.

    Returns:
        The check.
    """
    env = environ if environ is not None else os.environ
    return named(
        f"env_set({', '.join(names)})",
        lambda: [Problem(kind, f"{n} is not set") for n in names if not env.get(n)],
    )


def reachable(name: str, url: str, *, timeout: float = 5.0, ok_status: Iterable[int] = ()) -> Check:
    """Create a check that a server answers at `url`.

    Any HTTP status is accepted unless `ok_status` is given. A refused or timed-out connection is
    reported as a problem with the operator's environment rather than with the target.

    Args:
        name: Name of the service, used in problem messages.
        url: URL to request.
        timeout: Connection timeout in seconds.
        ok_status: HTTP status codes accepted. When empty, any status is accepted.

    Returns:
        The check.
    """
    wanted = set(ok_status)

    def check() -> list[Problem]:
        """Request the URL and report a problem if it does not answer as expected.

        Returns:
            A list containing one problem, or an empty list.
        """
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
    """Create a check that the results destination resolves, is configured and accepts a write.

    A missing destination or an unavailable sink produces a warning, and results then remain in
    the local run directory.

    Args:
        uri: Results destination URI, or None when no destination is configured.

    Returns:
        The check.
    """

    def check() -> list[Problem]:
        """Resolve the destination and perform a test write.

        Returns:
            A list containing one problem, or an empty list.
        """
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
    """Create a check that every notifier spec parses and its required variables are set.

    Running this before the run reports a configuration error before any case runs, rather than
    when the notification is sent.

    Args:
        specs: Notifier specs. Each may contain several comma-separated specs.

    Returns:
        The check.
    """

    def check() -> list[Problem]:
        """Parse each notifier spec and report those that are invalid.

        Returns:
            One problem per invalid spec.
        """
        problems = []
        for spec in specs:
            for one in (s for s in spec.split(",") if s.strip()):
                try:
                    parse_spec(one)
                except NotifierError as exc:
                    problems.append(Problem("notification", f"{one.split(':', 1)[0]}: {exc}"))
        return problems

    return named("notifiers_valid", check)
