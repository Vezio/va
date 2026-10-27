"""Cases, targets, selection and the runner that turns them into result records.

Two orthogonal axes:

- a **target** is *where* a case runs (an engine, a router, a gateway, an environment). It has
  a name and the capabilities its profile declares; anything else on it is the app's own;
- a **case** is *what* is proven. It receives a target and knows nothing of its internals, so
  one case written once yields one comparable result per target.

A case declares what it `needs`; a target that does not declare it yields a skip of class
`undeclared`, never a silent pass. Cases carry `tags` for selection and `variants` for
parametrisation.

Opt-in execution controls, all off by default:

- `workers > 1` runs cases concurrently. Cases marked `exclusive` (they measure something
  another request would disturb: a cache, a latency, a rate limit) still run one at a time,
  after the concurrent ones. Records are written and hooks called from the runner's own
  thread, in completion order; `run_all` returns them in plan order.
- `case_timeout_s` (or a case's own `timeout_s`) gives up on a case that has not finished: it
  is recorded as an `error` and the run goes on. Python cannot stop a thread, so the abandoned
  case may keep running in the background; its later evidence writes are dropped.

The runner never retries a case: a retried request hides the failure under test.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from .evidence import CheckFailed, Journal
from .hooks import HookChain, RunHooks
from .layout import RunFile
from .log import get_logger
from .model import ResultRecord, SkipClasses, Verdict
from .rundir import RunDir

log = get_logger("runner")


class Target(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> frozenset[str]: ...


S = TypeVar("S")  # the app's shared state
T = TypeVar("T", bound=Target)


class Skipped(Exception):
    def __init__(self, skip_class: str, reason: str):
        super().__init__(reason)
        self.skip_class = skip_class


class BudgetExceeded(Exception):
    """The run's time budget is spent: no new case starts, and the run ends interrupted."""


class CaseTimeout(Exception):
    """A case did not finish within its timeout."""


class Context(Generic[S, T]):
    """What a case receives: its target and variant, the app's shared state, the journal.

    With `workers > 1`, cases share `state` across threads: anything on it that a case mutates
    must be thread-safe."""

    def __init__(self, run: RunDir, case_id: str, target: T, variant: str, state: S | None):
        self.run = run
        self.case_id = case_id
        self.target = target
        self.variant = variant
        self.state = state
        self.journal = Journal(run.redactor)
        self.abandoned = threading.Event()  # set when the case timed out

    def check(self, name: str, observed: Any, expected: Any, ok: bool):
        return self.journal.check(name, observed, expected, ok)

    def require(self, name: str, observed: Any, expected: Any, ok: bool | None = None):
        return self.journal.require(name, observed, expected, ok)

    def fail(self, name: str, observed: Any, expected: Any) -> None:
        self.journal.fail(name, observed, expected)

    def skip(self, skip_class: str, reason: str) -> None:
        raise Skipped(skip_class, reason)

    @property
    def evidence_dir(self) -> str:
        folder = f"{RunFile.EVIDENCE}/{self.target.name}/{self.case_id}"
        return f"{folder}[{self.variant}]" if self.variant else folder

    def save(self, name: str, content: str) -> None:
        """Write evidence for this case (redacted) under evidence/<target>/<case>/. Dropped
        once the case has been abandoned after a timeout."""
        if not self.abandoned.is_set():
            self.run.write_text(f"{self.evidence_dir}/{name}", content)


CaseFunc = Callable[[Context[Any, Any]], None]


@dataclass(frozen=True)
class Case:
    case_id: str
    func: CaseFunc
    needs: frozenset[str] = frozenset()
    variants: tuple[str, ...] = ("",)
    tags: frozenset[str] = frozenset()
    doc: str = ""
    exclusive: bool = False  # never runs alongside another case
    timeout_s: float | None = None  # overrides the runner's case timeout


@dataclass(frozen=True)
class Selector:
    """Which cases run: ids containing any pattern, carrying every tag in `tags` and none in
    `exclude_tags`. Empty means everything."""

    patterns: tuple[str, ...] = ()
    tags: frozenset[str] = frozenset()
    exclude_tags: frozenset[str] = frozenset()

    def matches(self, case: Case) -> bool:
        if self.patterns and not any(p in case.case_id for p in self.patterns):
            return False
        return self.tags <= case.tags and not (self.exclude_tags & case.tags)


@dataclass
class CaseRegistry:
    """Cases register themselves; an app owns one registry per suite family."""

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
        def decorate(func: CaseFunc) -> CaseFunc:
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
        if case.case_id in self.cases:
            raise ValueError(f"case {case.case_id!r} is registered twice")
        self.cases[case.case_id] = case

    def select(self, selector: Selector | None = None) -> list[Case]:
        selector = selector or Selector()
        return sorted(
            (c for c in self.cases.values() if selector.matches(c)), key=lambda c: c.case_id
        )


@dataclass(frozen=True)
class Unit:
    """One planned execution: a case, on a target, with a variant."""

    index: int
    case: Case
    target: Any
    variant: str


def plan(cases: Sequence[Case], targets: Sequence[Target]) -> list[Unit]:
    units: list[Unit] = []
    for target in targets:
        for case in cases:
            for variant in case.variants:
                units.append(Unit(len(units), case, target, variant))
    return units


class Runner(Generic[S]):
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
        self.deadline = deadline  # a `clock()` value after which nothing new starts
        self.workers = workers
        self.case_timeout_s = case_timeout_s
        self.clock = clock

    # --- scheduling

    def run_all(self, cases: Sequence[Case], targets: Sequence[Target]) -> list[ResultRecord]:
        """Every case once per target and variant, each record written as it finishes. An
        interrupt or BudgetExceeded propagates; what finished is already on disk."""
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
        if self.deadline is not None and self.clock() > self.deadline:
            raise BudgetExceeded("the run's time budget is spent")

    def _finish(self, unit: Unit, record: ResultRecord, done: dict[int, ResultRecord]) -> None:
        """Only the runner's own thread writes records and calls hooks."""
        self.run.add_result(record)
        self.hooks.on_result(self.run, record)
        done[unit.index] = record

    def _concurrently(self, units: list[Unit], done: dict[int, ResultRecord]) -> None:
        work: queue.SimpleQueue[Unit] = queue.SimpleQueue()
        for unit in units:
            work.put(unit)
        results: queue.SimpleQueue[Any] = queue.SimpleQueue()
        stop = threading.Event()
        finished = object()  # a worker puts this when it has no more work

        def worker() -> None:
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
            except BaseException as exc:  # noqa: BLE001 -- surfaced in the runner's thread
                results.put(exc)
            finally:
                results.put(finished)

        # Daemon threads: a case that hangs must never keep the process alive at exit.
        threads = [
            threading.Thread(target=worker, name=f"cli-framework-worker-{i}", daemon=True)
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
            stop.set()  # no new case starts; running ones are abandoned
            raise
        if stop.is_set():
            raise BudgetExceeded("the run's time budget is spent")

    # --- one case

    def execute(self, unit: Unit) -> ResultRecord:
        case, target, variant = unit.case, unit.target, unit.variant
        ctx: Context[S, Any] = Context(self.run, case.case_id, target, variant, self.state)
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
            except AssertionError as exc:  # CheckFailed included
                verdict = Verdict.FAIL
                reason = str(exc) or "assertion failed"
                if not isinstance(exc, CheckFailed):
                    ctx.save("assertion.txt", traceback.format_exc())
            except CaseTimeout as exc:
                verdict, reason = Verdict.ERROR, str(exc)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:  # noqa: BLE001 -- the case could not reach a verdict
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
        """Call the case, within its timeout when one applies."""
        timeout = case.timeout_s if case.timeout_s is not None else self.case_timeout_s
        if timeout is None:
            case.func(ctx)
            return
        outcome: list[BaseException | None] = []

        def body() -> None:
            try:
                case.func(ctx)
                outcome.append(None)
            except BaseException as exc:  # noqa: BLE001 -- re-raised in the caller's thread
                outcome.append(exc)

        thread = threading.Thread(
            target=body, name=f"cli-framework-case-{case.case_id}", daemon=True
        )
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            ctx.abandoned.set()
            raise CaseTimeout(f"did not finish within {timeout:g}s; abandoned")
        if outcome and outcome[0] is not None:
            raise outcome[0]
