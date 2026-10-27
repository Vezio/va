# Writing and registering cases

A case is a Python function that verifies one behaviour of a target. This guide explains how a
tool registers its cases, how the framework finds and runs them, and how to add new ones. The
examples come from [`modelcheck`](../examples/modelcheck) and
[`httpcheck`](../examples/httpcheck); a tool created with `scripts/new-app.sh` has the same
layout.

## How registration works

Each tool owns one `CaseRegistry`. Case functions are added to it by a decorator when their
module is imported, and the tool's `run` command passes the selected cases to the `Session`.

```
src/modelcheck/
├── registry.py          REGISTRY = CaseRegistry()
├── cases/
│   ├── __init__.py      imports every case module, re-exports REGISTRY
│   ├── chat.py          @REGISTRY.case("chat.completion", ...)
│   ├── stream.py        @REGISTRY.case("stream.well_formed", ...)
│   └── ...
└── cli.py               from modelcheck.cases import REGISTRY
                         Session(cases=REGISTRY.select(options.selector), ...)
```

The sequence at start-up is:

1. `cli.py` imports `modelcheck.cases`.
2. `cases/__init__.py` imports each case module.
3. Each case module imports `REGISTRY` from `modelcheck.registry`, and its `@REGISTRY.case`
   decorators add the functions to the registry as the module is executed.
4. `cases/__init__.py` re-exports `REGISTRY`, which now holds every case.
5. The `run` command selects cases with `REGISTRY.select(...)`, applying `-k`, `--tag` and
   `--exclude-tag`, and passes them to the `Session`.

The registry lives in its own module, `registry.py`, so that case modules and the command code
import it from one place and no module imports from a package that is still initialising.

### The registry module

```python
# src/modelcheck/registry.py
"""The registry that holds every modelcheck case."""

from va import CaseRegistry

REGISTRY = CaseRegistry()
```

### A case module

```python
# src/modelcheck/cases/models.py
"""Cases that check the model list."""

from __future__ import annotations

from va import Context

from modelcheck.registry import REGISTRY


@REGISTRY.case("models.lists_declared_model", needs={"models"}, tags={"smoke"})
def lists_declared_model(ctx: Context) -> None:
    """Verify that GET /v1/models returns 200 and lists the declared model."""
    t = ctx.target
    exchange = ctx.state.client(t).get("/v1/models", case_id=ctx.case_id)
    ctx.require("http_status", exchange.status, 200)
    ids = [m.get("id") for m in exchange.json().get("data", [])]
    ctx.require("model_listed", t.model in ids, True)
```

### The package that loads the modules

```python
# src/modelcheck/cases/__init__.py
"""The modelcheck cases."""

from modelcheck.cases import auth, chat, errors, models, stream
from modelcheck.registry import REGISTRY

__all__ = ["REGISTRY", "auth", "chat", "errors", "models", "stream"]
```

A case module that is not imported here is never loaded, so its cases are never registered and
never run. Listing the modules in `__all__` records that they are imported deliberately.

### The command that runs them

```python
# src/modelcheck/cli.py (excerpt)
from modelcheck.cases import REGISTRY

...
        return Session(
            tool=app.prog,
            run=options.run,
            cases=REGISTRY.select(options.selector),
            targets=profile.targets,
            ...
        ).execute()
```

Import `REGISTRY` from the `cases` package, not from `registry.py`. Importing the package is
what loads the case modules; importing `registry.py` alone returns an empty registry.

## Adding a case

To add a case to an existing group, add a decorated function to that group's module. Nothing
else needs to change.

To add a new group:

1. Create `src/<tool>/cases/<group>.py` with one or more decorated functions.
2. Import the module in `src/<tool>/cases/__init__.py` and add it to `__all__`.
3. Add a defect to the tool's simulated service and a test that shows the case failing against
   it. See [Getting started: Prove the case can fail](getting-started.md#5-prove-the-case-can-fail).
4. Run `<tool> profile show <profile>` to confirm the case is listed, and on which targets it
   runs or is skipped.

## The `case` decorator

```python
@REGISTRY.case(
    "stream.well_formed",       # the case identifier
    needs={"stream"},            # capabilities a target must declare
    variants=("a", "b"),         # run once per variant
    tags={"smoke"},              # used by --tag and --exclude-tag
    exclusive=False,             # run alone, never concurrently with other cases
    timeout_s=None,              # per-case timeout in seconds
)
def well_formed(ctx: Context) -> None:
    """Verify that a streamed completion is a well-formed event stream."""
```

| Argument | Description |
|---|---|
| `case_id` | Unique identifier, written `group.behaviour` (for example `errors.unknown_model`). The part before the dot groups cases in reports. Identifiers are the keys for comparing runs, so keep them stable once published. |
| `needs` | Capabilities the target must declare in the profile. On a target that lacks one, the case is skipped with the class `undeclared` and the missing capability is recorded. |
| `variants` | Run the case once per value; the case reads `ctx.variant`. Results are reported as `case_id[variant]`. |
| `tags` | Labels for selection: `--tag smoke` runs only tagged cases, `--exclude-tag perf` leaves them out. |
| `exclusive` | When the run uses `--workers`, an exclusive case still runs on its own, after the concurrent ones. Use it for cases that measure latency, caching or rate limits. |
| `timeout_s` | Abandon the case after this many seconds and record it as an `error`. Overrides `--case-timeout`. |

The function's docstring becomes the case description in reports and in `profile show`, so write
it as a one-line statement of the behaviour verified.

Registering two cases with the same identifier raises `ValueError` when the second module is
imported, so a duplicate is detected before any case runs.

## Inside a case

A case receives a `Context`:

| Member | Description |
|---|---|
| `ctx.target` | The target from the profile, with everything declared for it |
| `ctx.variant` | The current variant, or an empty string |
| `ctx.state` | The tool's shared state, for example its clients (shared across workers, so keep it thread-safe) |
| `ctx.case_id` | The case identifier, used to label recorded exchanges |
| `ctx.check(name, observed, expected, ok)` | Record an observation without deciding the verdict |
| `ctx.require(name, observed, expected[, ok])` | Record an observation and fail the case unless it holds. Without `ok`, the check holds when `observed == expected` |
| `ctx.fail(name, observed, expected)` | Record a failed observation and stop the case |
| `ctx.skip(skip_class, reason)` | Stop the case and record it as skipped, with a skip class (see [Extending: Skip reasons](extending.md#skip-reasons)) |
| `ctx.save(name, text)` | Write a file of evidence under `evidence/<target>/<case>/` |

The verdict follows from what the case does:

| The case | Verdict |
|---|---|
| returns normally | `pass` |
| fails a `require`, or calls `fail` | `fail`: the target behaved incorrectly |
| calls `skip`, or the target lacks a capability in `needs` | `skip`, with its class |
| raises any other exception, or exceeds its timeout | `error`: the case could not reach a verdict; the traceback is saved |

Record observations before deciding. A failed case then shows the value that caused it, and a
passed case shows what it verified.

Requests made by a case are not retried. A retry could turn an intermittent failure in the target
into a pass, so the failure is reported as it occurred.

Read expected values from the profile rather than branching on the target's name. When targets
legitimately behave differently, declare the difference in the profile; see
[Profiles: Reading declarations in cases](profiles.md#reading-declarations-in-cases).

## Registering cases programmatically

The decorator suits cases written by hand. When cases are generated from data, such as one case
per API endpoint listed in a file, build `Case` objects and add them with `REGISTRY.add`:

```python
# src/mytool/cases/endpoints.py
"""One availability case per endpoint in ENDPOINTS."""

from __future__ import annotations

from va import Case, Context

from mytool.registry import REGISTRY

ENDPOINTS = {"users": "/v1/users", "orders": "/v1/orders"}


def _answers(path: str):
    """Return a case function that checks that `path` answers 200."""

    def case(ctx: Context) -> None:
        """Verify that the endpoint answers 200."""
        exchange = ctx.state.client(ctx.target).get(path, case_id=ctx.case_id)
        ctx.require("http_status", exchange.status, 200)

    return case


for name, path in ENDPOINTS.items():
    REGISTRY.add(
        Case(
            f"endpoints.{name}",
            _answers(path),
            needs=frozenset({"json"}),
            doc=f"Verify that {path} answers 200.",
        )
    )
```

Each generated case has its own identifier, so it is selected, reported and compared like any
other. Import the module in `cases/__init__.py` as usual.

## Selecting cases

| Command line | Runs |
|---|---|
| `mytool run --profile p.yaml` | every registered case |
| `mytool run --profile p.yaml -k chat` | cases whose identifier contains `chat` (repeat `-k` for more) |
| `mytool run --profile p.yaml --tag smoke` | cases tagged `smoke` |
| `mytool run --profile p.yaml --exclude-tag perf` | every case not tagged `perf` |

`REGISTRY.select(Selector(...))` applies the same rules in code. Selected cases run in
identifier order, once per target and variant. Repeated `-k` options select a case that matches
any of them; repeated `--tag` options select a case that carries all of them.

## Common problems

| Symptom | Cause |
|---|---|
| A new case does not appear in `profile show` or in results | Its module is not imported in `cases/__init__.py` |
| The registry is empty | `REGISTRY` was imported from `registry.py` instead of from the `cases` package |
| `ValueError: case '...' is registered twice` | Two functions use the same `case_id` |
| A case is always skipped as `undeclared` | No target in the profile declares a capability listed in `needs` |
| A case reports `error` instead of `fail` | An exception other than a failed check was raised, for example a `KeyError` while reading a response; check the saved traceback |
