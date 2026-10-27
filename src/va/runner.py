"""Cases, targets, case selection, and the runner that produces result records.

The module separates two independent concepts:

- A target is where a case runs, such as an engine, a router, a gateway or an environment. It
  has a name and the capabilities declared in its profile. Any other attributes belong to the
  application.
- A case is what is verified. It receives a target and does not depend on the target's
  internals, so a single case definition produces one comparable result per target.

A case declares the capabilities it needs. When a target does not declare one of them, the case
is skipped with skip class `undeclared` rather than passing silently. Cases carry `tags` for
selection and `variants` for parametrisation.

The following execution controls are optional and disabled by default:

- `workers > 1` runs cases concurrently. Cases marked `exclusive`, which measure something that
  another request would disturb (such as a cache, a latency or a rate limit), still run one at a
  time after the concurrent cases. Records are written and hooks are called from the runner's
  own thread in completion order, and `run_all` returns the records in plan order.
- `case_timeout_s`, or a case's own `timeout_s`, abandons a case that has not finished in time.
  The case is recorded as an `error` and the run continues. Python cannot stop a thread, so the
  abandoned case may continue to run in the background, and any evidence it writes afterwards
  is discarded.

The runner does not retry cases, because a retried request would hide the failure being tested.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar, cast

from .evidence import CheckFailed, Journal
from .hooks import HookChain, RunHooks
from .layout import RunFile
from .log import get_logger
from .model import ResultRecord, SkipClasses, Verdict
from .rundir import RunDir

log = get_logger("runner")


class Target(Protocol):
    """Protocol for a target that cases run against."""

    @property
    def name(self) -> str:
        """The target name, used in result records and evidence paths."""
        ...

    @property
    def capabilities(self) -> frozenset[str]:
        """The capabilities the target's profile declares."""
        ...


# The application's shared state type.
S = TypeVar("S")
T = TypeVar("T", bound=Target)


class Skipped(Exception):
    """Exception a case raises to report that it was skipped.

    Args:
        skip_class: The skip class, which must be one the runner recognises.
        reason: Explanation of why the case was skipped.

    Attributes:
        skip_class: The skip class.
    """

    def __init__(self, skip_class: str, reason: str):
        super().__init__(reason)
        self.skip_class = skip_class


class BudgetExceeded(Exception):
    """Exception raised when the run's time budget is spent.

    No further case is started, and the run ends as interrupted.
    """


class CaseTimeout(Exception):
    """Exception raised when a case does not finish within its timeout."""


class Context(Generic[S, T]):
    """The objects a case receives: its target and variant, the shared state and the journal.

    When `workers > 1`, cases share `state` across threads, so any part of it that a case
    modifies must be thread-safe.

    Args:
        run: The run directory.
        case_id: Identifier of the case.
        target: The target the case runs against.
        variant: The variant being run, or an empty string.
        state: The application's shared state.

    Attributes:
        run: The run directory.
        case_id: Identifier of the case.
        target: The target the case runs against.
        variant: The variant being run, or an empty string.
        state: The application's shared state.
        journal: The journal of checks recorded by the case.
        abandoned: Event set when the case has timed out and been abandoned.
    """

    def __init__(self, run: RunDir, case_id: str, target: T, variant: str, state: S):
        self.run = run
        self.case_id = case_id
        self.target = target
        self.variant = variant
        self.state = state
        self.journal = Journal(run.redactor)
        self.abandoned = threading.Event()

    def check(self, name: str, observed: Any, expected: Any, ok: bool):
        """Record a check without raising. See `Journal.check`.

        Args:
            name: Name of the check.
            observed: Value observed.
            expected: Value or condition expected.
            ok: Whether the check passed.

        Returns:
            The recorded check.
        """
        return self.journal.check(name, observed, expected, ok)

    def require(self, name: str, observed: Any, expected: Any, ok: bool | None = None):
        """Record a check and raise if it did not pass. See `Journal.require`.

        Args:
            name: Name of the check.
            observed: Value observed.
            expected: Value or condition expected.
            ok: Whether the check passed. When None, the check passes if `observed` equals
                `expected`.

        Returns:
            The recorded check.

        Raises:
            CheckFailed: If the check did not pass.
        """
        return self.journal.require(name, observed, expected, ok)

    def fail(self, name: str, observed: Any, expected: Any) -> None:
        """Record a failed check and raise. See `Journal.fail`.

        Args:
            name: Name of the check.
            observed: Value observed.
            expected: Value or condition expected.

        Raises:
            CheckFailed: Always.
        """
        self.journal.fail(name, observed, expected)

    def skip(self, skip_class: str, reason: str) -> None:
        """Skip the case.

        Args:
            skip_class: The skip class.
            reason: Explanation of why the case was skipped.

        Raises:
            Skipped: Always.
        """
        raise Skipped(skip_class, reason)

    @property
    def evidence_dir(self) -> str:
        """The run-relative directory for this case's evidence, including the variant if any."""
        folder = f"{RunFile.EVIDENCE}/{self.target.name}/{self.case_id}"
        return f"{folder}[{self.variant}]" if self.variant else folder

    def save(self, name: str, content: str) -> None:
        """Write a redacted evidence file for this case under `evidence/<target>/<case>/`.

        The write is discarded if the case has been abandoned after a timeout.

        Args:
            name: File name within the case's evidence directory.
            content: Text to write.
        """
        if not self.abandoned.is_set():
            self.run.write_text(f"{self.evidence_dir}/{name}", content)


CaseFunc = Callable[[Context[Any, Any]], None]


@dataclass(frozen=True)
class Case:
    """A registered case.

    Attributes:
        case_id: Unique identifier of the case.
        func: The function that implements the case.
        needs: Capabilities a target must declare for the case to run.
        variants: Variants to run the case with. A single empty string means no variants.
        tags: Tags used for selection.
        doc: Description of the case, taken from the function's docstring.
        exclusive: Whether the case must not run concurrently with any other case.
        timeout_s: Timeout in seconds that overrides the runner's case timeout, or None.
    """

    case_id: str
    func: CaseFunc
    needs: frozenset[str] = frozenset()
    variants: tuple[str, ...] = ("",)
    tags: frozenset[str] = frozenset()
    doc: str = ""
    exclusive: bool = False
    timeout_s: float | None = None


@dataclass(frozen=True)
class Selector:
    """Criteria for selecting the cases to run.

    A case is selected when its identifier contains any of `patterns`, it carries every tag in
    `tags`, and it carries none of the tags in `exclude_tags`. Empty criteria select every case.

    Attributes:
        patterns: Substrings matched against case identifiers.
        tags: Tags a case must carry.
        exclude_tags: Tags a case must not carry.
    """

    patterns: tuple[str, ...] = ()
    tags: frozenset[str] = frozenset()
    exclude_tags: frozenset[str] = frozenset()

    def matches(self, case: Case) -> bool:
        """Return whether the case meets the selection criteria.

        Args:
            case: The case to test.

        Returns:
            True if the case is selected.
        """
        if self.patterns and not any(p in case.case_id for p in self.patterns):
            return False
        return self.tags <= case.tags and not (self.exclude_tags & case.tags)


@dataclass
class CaseRegistry:
    """Registry of cases. An application owns one registry per suite family.

    Attributes:
        cases: The registered cases, by identifier.
    """

    cases: dict[str, Case] = field(default_factory=dict)

    def case(
        self,
        case_id: str,
        *,
        needs: Iterable[str] = (),
        variants: Sequence[str] = ("",),
        tags: Iterable[str] = (),
        exclusive: bool = False,
        timeout_s: float | None = None,
    ) -> Callable[[CaseFunc], CaseFunc]:
        """Return a decorator that registers a function as a case.

        The function's docstring becomes the case description.

        Args:
            case_id: Unique identifier of the case.
            needs: Capabilities a target must declare for the case to run.
            variants: Variants to run the case with.
            tags: Tags used for selection.
            exclusive: Whether the case must not run concurrently with any other case.
            timeout_s: Timeout in seconds that overrides the runner's case timeout.

        Returns:
            The decorator, which returns the function unchanged.
        """

        def decorate(func: CaseFunc) -> CaseFunc:
            """Register `func` as a case and return it unchanged."""
            self.add(
                Case(
                    case_id,
                    func,
                    frozenset(needs),
                    tuple(variants) or ("",),
                    frozenset(tags),
                    (func.__doc__ or "").strip(),
                    exclusive,
                    timeout_s,
                )
            )
            return func

        return decorate

    def add(self, case: Case) -> None:
        """Register a case.

        Args:
            case: The case to register.

        Raises:
            ValueError: If a case with the same identifier is already registered.
        """
        if case.case_id in self.cases:
            raise ValueError(f"case {case.case_id!r} is registered twice")
        self.cases[case.case_id] = case

    def select(self, selector: Selector | None = None) -> list[Case]:
        """Return the cases that match a selector, sorted by identifier.

        Args:
            selector: Selection criteria. When None, every case is selected.

        Returns:
            The matching cases.
        """
        selector = selector or Selector()
        return sorted(
            (c for c in self.cases.values() if selector.matches(c)), key=lambda c: c.case_id
        )


@dataclass(frozen=True)
class Unit:
    """One planned execution of a case on a target with a variant.

    Attributes:
        index: Position of the unit in the plan.
        case: The case to run.
        target: The target to run it against.
        variant: The variant to run, or an empty string.
    """

    index: int
    case: Case
    target: Any
    variant: str


def plan(cases: Sequence[Case], targets: Sequence[Target]) -> list[Unit]:
    """Return one unit per combination of target, case and variant.

    Units are ordered by target, then case, then variant.

    Args:
        cases: The cases to run.
        targets: The targets to run them against.

    Returns:
        The planned units, indexed in order.
    """
    units: list[Unit] = []
    for target in targets:
        for case in cases:
            for variant in case.variants:
                units.append(Unit(len(units), case, target, variant))
    return units


class Runner(Generic[S]):
    """Runner that executes cases against targets and records their results.

    Args:
        run: The run directory that receives records and evidence.
        state: The application's shared state, passed to every case.
        labels: Labels copied onto every result record.
        hooks: Hooks called as the run progresses.
        skip_classes: Skip classes a case may use. Defaults to `SkipClasses.default()`.
        deadline: A `clock()` value after which no new case is started, or None for no limit.
        workers: Number of cases to run concurrently.
        case_timeout_s: Default case timeout in seconds, or None for no timeout.
        clock: Monotonic clock used for durations and the deadline.

    Attributes:
        run: The run directory.
        state: The application's shared state.
        labels: Labels copied onto every result record.
        hooks: Hooks called as the run progresses.
        skip_classes: Skip classes a case may use.
        deadline: A `clock()` value after which no new case is started, or None.
        workers: Number of cases to run concurrently.
        case_timeout_s: Default case timeout in seconds, or None.
        clock: Monotonic clock.

    Raises:
        ValueError: If `workers` is less than 1.
    """

    def __init__(
        self,
        run: RunDir,
        *,
        state: S | None = None,
        labels: dict[str, str] | None = None,
        hooks: RunHooks | None = None,
        skip_classes: SkipClasses | None = None,
        deadline: float | None = None,
        workers: int = 1,
        case_timeout_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        if workers < 1:
            raise ValueError("workers must be at least 1")
        self.run = run
        self.state = state
        self.labels = labels or {}
        self.hooks = hooks or HookChain()
        self.skip_classes = skip_classes or SkipClasses.default()
        self.deadline = deadline
        self.workers = workers
        self.case_timeout_s = case_timeout_s
        self.clock = clock

    def run_all(self, cases: Sequence[Case], targets: Sequence[Target]) -> list[ResultRecord]:
        """Run every case once per target and variant, writing each record as it finishes.

        An interrupt or `BudgetExceeded` propagates to the caller. Records of the cases that
        finished before it have already been written.

        Args:
            cases: The cases to run.
            targets: The targets to run them against.

        Returns:
            The result records of the finished units, in plan order.

        Raises:
            BudgetExceeded: If the deadline passes before every unit has started.
        """
        units = plan(cases, targets)
        shared = [u for u in units if not u.case.exclusive]
        alone = [u for u in units if u.case.exclusive]
        done: dict[int, ResultRecord] = {}
        if self.workers > 1 and len(shared) > 1:
            self._concurrently(shared, done)
        else:
            alone = shared + alone
        for unit in alone:
            self._check_budget()
            self._finish(unit, self.execute(unit), done)
        return [done[u.index] for u in units if u.index in done]

    def _check_budget(self) -> None:
        """Raise if the deadline has passed.

        Raises:
            BudgetExceeded: If the deadline has passed.
        """
        if self.deadline is not None and self.clock() > self.deadline:
            raise BudgetExceeded("the run's time budget is spent")

    def _finish(self, unit: Unit, record: ResultRecord, done: dict[int, ResultRecord]) -> None:
        """Write a record, call the hooks and store the record by unit index.

        This method is called only from the runner's own thread, so records are written and
        hooks are called from a single thread.

        Args:
            unit: The unit that finished.
            record: Its result record.
            done: Finished records by unit index, updated in place.
        """
        self.run.add_result(record)
        self.hooks.on_result(self.run, record)
        done[unit.index] = record

    def _concurrently(self, units: list[Unit], done: dict[int, ResultRecord]) -> None:
        """Run units on a pool of worker threads.

        Workers execute units and pass the records back through a queue. The runner's thread
        writes each record as it arrives. On an interrupt or an exception from a worker, no
        new unit is started and units still running are abandoned.

        Args:
            units: The units to run.
            done: Finished records by unit index, updated in place.

        Raises:
            BudgetExceeded: If the deadline passed before every unit started.
        """
        work: queue.SimpleQueue[Unit] = queue.SimpleQueue()
        for unit in units:
            work.put(unit)
        results: queue.SimpleQueue[Any] = queue.SimpleQueue()
        stop = threading.Event()
        # Sentinel a worker puts on the results queue when it has no more work.
        finished = object()

        def worker() -> None:
            """Execute units from the work queue until it is empty or the run stops.

            Each result, or any exception raised, is put on the results queue, followed by the
            `finished` sentinel.
            """
            try:
                while not stop.is_set():
                    try:
                        unit = work.get_nowait()
                    except queue.Empty:
                        return
                    if self.deadline is not None and self.clock() > self.deadline:
                        stop.set()
                        return
                    results.put((unit, self.execute(unit)))
            # Exceptions are passed to the runner's thread and re-raised there.
            except BaseException as exc:  # noqa: BLE001
                results.put(exc)
            finally:
                results.put(finished)

        # Daemon threads ensure that a case that hangs does not keep the process alive at exit.
        threads = [
            threading.Thread(target=worker, name=f"va-worker-{i}", daemon=True)
            for i in range(min(self.workers, len(units)))
        ]
        for thread in threads:
            thread.start()
        running = len(threads)
        try:
            while running:
                item = results.get()
                if item is finished:
                    running -= 1
                elif isinstance(item, BaseException):
                    stop.set()
                    raise item
                else:
                    unit, record = item
                    self._finish(unit, record, done)
        except KeyboardInterrupt:
            # Prevent new cases from starting. Cases already running are abandoned.
            stop.set()
            raise
        if stop.is_set():
            raise BudgetExceeded("the run's time budget is spent")

    def execute(self, unit: Unit) -> ResultRecord:
        """Run one unit and return its result record.

        A target that does not declare a needed capability yields a skip of class `undeclared`.
        Otherwise the case's outcome is mapped to a verdict: a normal return is a pass, an
        assertion error is a failure, a `Skipped` with a recognised class is a skip, and any
        other exception or a timeout is an error. A pass with no recorded checks is logged as
        a warning.

        Args:
            unit: The unit to run.

        Returns:
            The result record. It is not written to the run directory by this method.

        Raises:
            KeyboardInterrupt: Propagated from the case.
            SystemExit: Propagated from the case.
        """
        case, target, variant = unit.case, unit.target, unit.variant
        # A runner created without state passes None to cases. An application that uses state
        # sets it when creating the runner.
        state = cast(S, self.state)
        ctx: Context[S, Any] = Context(self.run, case.case_id, target, variant, state)
        started = self.clock()
        skip_class: str | None = None
        reason = ""
        missing = sorted(case.needs - target.capabilities)
        if missing:
            verdict, skip_class = Verdict.SKIP, SkipClasses.UNDECLARED
            reason = f"{target.name} does not declare {', '.join(missing)}"
        else:
            try:
                self._invoke(case, ctx)
                verdict = Verdict.PASS
            except Skipped as exc:
                verdict, skip_class, reason = Verdict.SKIP, exc.skip_class, str(exc)
                if skip_class not in self.skip_classes:
                    verdict, skip_class = Verdict.ERROR, None
                    reason = f"skipped with unknown skip class {exc.skip_class!r}: {exc}"
            # This also catches CheckFailed, which subclasses AssertionError.
            except AssertionError as exc:
                verdict = Verdict.FAIL
                reason = str(exc) or "assertion failed"
                if not isinstance(exc, CheckFailed):
                    ctx.save("assertion.txt", traceback.format_exc())
            except CaseTimeout as exc:
                verdict, reason = Verdict.ERROR, str(exc)
            except (KeyboardInterrupt, SystemExit):
                raise
            # Any other exception means the case could not reach a verdict.
            except Exception as exc:  # noqa: BLE001
                verdict, reason = Verdict.ERROR, f"{type(exc).__name__}: {exc}"
                ctx.save("traceback.txt", traceback.format_exc())
        if verdict is Verdict.PASS and not ctx.journal.checks:
            log.warning("%s passed with no recorded checks: it proved nothing", case.case_id)
        return ResultRecord(
            run_id=self.run.run_id,
            case_id=case.case_id,
            verdict=verdict,
            target=target.name,
            variant=variant,
            duration_s=round(self.clock() - started, 3),
            reason=reason.splitlines()[0] if reason else "",
            skip_class=skip_class,
            checks=list(ctx.journal.checks),
            labels=dict(self.labels),
        )

    def _invoke(self, case: Case, ctx: Context[S, Any]) -> None:
        """Call the case function, enforcing its timeout when one applies.

        With a timeout, the case runs on a daemon thread. If it does not finish in time, the
        context is marked as abandoned and the thread is left running.

        Args:
            case: The case to call.
            ctx: The context passed to the case.

        Raises:
            CaseTimeout: If the case does not finish within its timeout.
            BaseException: Any exception raised by the case.
        """
        timeout = case.timeout_s if case.timeout_s is not None else self.case_timeout_s
        if timeout is None:
            case.func(ctx)
            return
        outcome: list[BaseException | None] = []

        def body() -> None:
            """Call the case and store its exception, or None on success, in `outcome`."""
            try:
                case.func(ctx)
                outcome.append(None)
            # The exception is re-raised in the caller's thread.
            except BaseException as exc:  # noqa: BLE001
                outcome.append(exc)

        thread = threading.Thread(target=body, name=f"va-case-{case.case_id}", daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            ctx.abandoned.set()
            raise CaseTimeout(f"did not finish within {timeout:g}s; abandoned")
        if outcome and outcome[0] is not None:
            raise outcome[0]
