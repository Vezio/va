# Getting started

From nothing to a tool of your own. You need Python 3.11 or newer, and `git`.

## 1. See it work

```sh
git clone https://github.com/Vezio/cli-framework && cd cli-framework
demo/local/run.sh
```

The demo installs itself into `.venv` on first use, starts a small serving stack on your
machine and checks it twice; [demo/README.md](../demo/README.md) explains what you are looking
at. Open one of the `report.html` files it points to: each case, with what it checked.

## 2. Create your tool

```sh
scripts/new-app.sh mytool ../mytool
cd ../mytool
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q          # the copied tests pass before you change anything
.venv/bin/mytool --help
```

`mytool` is your tool's name: a lowercase Python identifier. The tool depends on the exact
commit of cli-framework you ran `new-app.sh` from (see its `pyproject.toml`); that commit must be
on GitHub. If you are changing the framework itself, `scripts/new-app.sh --local mytool
../mytool` depends on your checkout instead.

You now have:

```
src/mytool/
  cli.py            the `run` and `profile` commands; job, publish, compare, notify are built in
  profile.py        what a profile may declare, and how it is validated
  client.py         a recording HTTP client: every request and response is kept as evidence
  cases/            the checks, one module per group
profiles/           an example profile
tests/              a fake server with switchable defects, and tests against it
container/          the runner image definition
deploy/             a Kubernetes CronJob, an example overlay, trigger.sh
.github/workflows/  tests on every push
```

It starts as a copy of `httpcheck`, which checks generic HTTP services. Keep what fits and
replace the rest.

## 3. Describe your system in a profile

```yaml
name: my-service

targets:
  - name: gateway
    base_url_env: GATEWAY_URL         # the variable's name, never the URL
    key_env: GATEWAY_TOKEN            # the variable's name, never the key
    capabilities: [health, json, auth]
```

Save it as `profiles/my-service.yaml`, then:

```sh
.venv/bin/mytool profile validate profiles/my-service.yaml   # every problem, with its path
.venv/bin/mytool profile show profiles/my-service.yaml       # what would run, what would skip
```

Your profile will grow with your cases: [Profiles](profiles.md) shows how to design one and how
the loader in `profile.py` validates it.

## 4. Write a case

Create `src/mytool/cases/models.py`:

```python
"""The model list."""

from __future__ import annotations

from cli_framework import Context

from . import REGISTRY


@REGISTRY.case("api.models_listed", needs={"json"}, tags={"smoke"})
def models_listed(ctx: Context) -> None:
    """GET /v1/models answers 200 and lists at least one model."""
    t = ctx.target
    exchange = ctx.state.client(t).get("/v1/models", case_id=ctx.case_id)
    ctx.require("http_status", exchange.status, 200)
    models = exchange.json().get("data", [])
    ctx.require("model_count", len(models), ">= 1", len(models) >= 1)
```

Then register it: add `models` to the import at the bottom of `src/mytool/cases/__init__.py`.
A module that is not imported there never runs.

```python
from . import api, health, models  # noqa: E402, F401  -- registration on import
```

- `ctx.require(name, observed, expected)` records the check, then fails the case unless observed
  equals expected; pass a fourth argument to decide yourself, as `model_count` does.
  `ctx.check(...)` records without deciding. Recording before deciding is what lets a report show
  the value that caused a failure, and what a pass actually checked.
- `needs={"json"}`: a target that does not declare `json` skips this case and says why. It is
  never silently passed.
- Never retry inside a case: a retry hides the failure the case exists to catch.

## 5. Prove the case can fail

A case that has never failed has not been tested. The tests run your tool against
`tests/fake_server.py`, a real local HTTP server; give it the endpoint, and a defect to switch on.

In `tests/fake_server.py`, add a field to `Defects`:

```python
    models: tuple[str, ...] = ("demo-model",)
```

and a route in `_answer`, before the final 404:

```python
                if self.path == "/v1/models":
                    data = [{"id": m} for m in d.models]
                    return self._send(200, json.dumps({"data": data}).encode(), "application/json")
```

Then, in `tests/test_mytool.py`, add a row to `test_each_case_fails_against_its_defect`:

```python
        ("models", (), "api.models_listed[]"),
```

```sh
.venv/bin/python -m pytest -q
```

The new row passes only if the case fails when the server lists no models. That is the proof.

## 6. Run it against your system

```sh
GATEWAY_URL=https://gateway.example.com GATEWAY_TOKEN=... \
  .venv/bin/mytool run --profile profiles/my-service.yaml --html
```

The run directory lands in `runs/`; its `report.html` shows every case and every check. Exit
code `0` means everything selected passed, `1` that cases failed, `3` that the environment was
not ready (a variable unset, a target unreachable) and nothing was tested.

## Next

- [Usage](usage.md): every command and flag, publishing and comparing runs, notifications.
- [Profiles](profiles.md): grow the profile as your cases grow.
- [Extending](extending.md): a different protocol, your own checks and notifiers.
- [Deploying](deploying.md): run it nightly, as a Kubernetes CronJob or in CI.
