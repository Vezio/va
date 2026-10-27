# Working in this repository

`src/va/` is the framework; `examples/httpcheck/` and `examples/modelcheck/` are complete tools
built on it. Read [docs/architecture.md](../docs/architecture.md), in particular its design
rules, before changing the framework, and [docs/cases.md](../docs/cases.md) before adding or
changing a case.

## Rules

- Do not use `print` (ruff `T20`). Use `log.get_logger` for diagnostics and `log.emit` for
  primary output.
- Attach redaction filters to handlers, not loggers, and read secrets through
  `REDACTOR.secret_from_env` or a notifier's `env:NAME` target.
- Keep vendors out of the core: a notification channel, results store or profile format is a
  plugin registered by name.
- Use the typed records in `model.py` and the enums for run files. Do not test an `ExitCode`
  for truthiness, because `ExitCode.OK` is `0`.
- Keep exit codes distinct: `1` is only for target failures, and `5` never replaces a failing
  verdict (`exit_codes.combine`).
- Do not retry requests under test; `retry` is for deliveries only.
- Keep `kube.py` read-only.
- Write and publish `MANIFEST.json` last.
- Keep optional features (concurrency, timeouts, extra outputs) off by default. Mark cases that
  measure latency, caching or rate limits `exclusive=True`.
- Do not commit hostnames, keys or cluster names. Profiles name environment variables; real
  values belong in `*.local.*` files, which Git ignores.
- Document every module, class, function and method with a Google-style docstring.

## Adding a case

1. Name it `group.behaviour` and keep the identifier stable, since runs are compared by it.
2. Declare the capabilities it needs in `needs`; do not test for a capability inside the case.
3. Record observations with `ctx.check` and decide with `ctx.require` or `ctx.fail`.
4. Read expected values from the target's declarations; do not branch on the target's name.
5. Add a defect to the example's fake server and a test showing the case fails against it, with
   the deciding check recorded as failed.
6. Skip only with `ctx.skip(class, reason)` and a class that describes the reason accurately.

## Before reporting a change as complete

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy src/va examples/httpcheck/src examples/modelcheck/src
```
