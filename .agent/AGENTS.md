# Working in this repo

`src/cli_framework/` is a library of primitives for CLIs that test deployed systems;
`examples/httpcheck/` is a complete app built on it. Read [../docs/design.md](../docs/design.md) before
changing a primitive: each rule there exists because breaking it produced a wrong answer.

## Hard rules

- **No `print`.** Ruff `T20`. Diagnostics through `log.get_logger`, primary output through
  `log.emit`.
- **Redaction filters live on handlers**, never only on a logger: a logger filter misses its
  child loggers' records.
- **Secrets enter through `REDACTOR.secret_from_env`** (or a notifier's `env:NAME` target), so
  they are registered as they are read.
- **No vendor in the core.** A channel, store or format is a plugin registered by name.
- **Typed records.** Run files are dataclasses from `model.py`; enums, not strings. Never test
  an `ExitCode` for truthiness: `OK` is 0.
- **Exit codes keep their meaning.** Never widen `1` to cover the environment; never let `5`
  mask a verdict (`exit_codes.combine`).
- **Never retry a test request.** `retry` is for deliveries only.
- **`kube.py` stays read-only.** A tool that must write gets its own explicit adapter.
- **The manifest is written and published last.**
- **Opt-in stays opt-in.** Concurrency, timeouts and extra outputs are off by default; a case
  that measures latency, caching or rate limits is `exclusive=True`. Hooks and record writes
  happen on the runner's thread only.
- **No hosts, keys or cluster names in committed files.** Profiles name variables; real values
  live in `*.local.*` files, which Git ignores.

## Before you say it works

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy src/cli_framework examples/httpcheck/src examples/modelcheck/src
```

A new case needs its defect in the fake server and a test proving the case fails against it,
with the deciding check recorded as not-ok.

## Skills

| Skill | Use it when |
|---|---|
| [adding-a-case](skills/adding-a-case/SKILL.md) | adding or changing a case, a capability or a command in an app |
