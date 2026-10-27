# Getting started

From nothing to a tool of your own in five steps. You need Python 3.11 or newer.

## 1. Install and run the example

```sh
git clone https://github.com/Vezio/cli-framework && cd cli-framework
scripts/install.sh            # creates .venv with the framework and the example app

demo/local/run.sh
```

`httpcheck` is the example tool. It checks HTTP services against a **profile** and writes a
**run directory**. Open the `report.html` the demo points you to, to see each case with what
it checked. [demo/README.md](../demo/README.md) explains the cases, and shows the same tool
running as a Kubernetes CronJob on a local kind cluster.

## 2. Create your tool

```sh
scripts/new-app.sh mytool ../mytool
cd ../mytool
python3 -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/mytool --help
.venv/bin/python -m pytest tests -q     # the copied tests pass before you change anything
```

You now have a copy of the example, renamed, depending on the framework:

```
src/mytool/
  cli.py        the app: its `run` and `profile` commands, plus the built-ins
  profile.py    what a profile may declare, and its validation
  client.py     a recording HTTP client (every exchange is kept as evidence)
  cases/        the checks, one module per group
profiles/       example profiles
tests/          a fake server with switchable defects, and tests against it
```

## 3. Describe the target in a profile

A profile says what a **correct** target does. It names environment variables for addresses
and keys; it never contains them.

```toml
name = "my-service"

[[targets]]
name = "gateway"
base_url_env = "GATEWAY_URL"
key_env = "GATEWAY_TOKEN"
capabilities = ["health", "json", "auth"]
```

`mytool profile validate profiles/my-service.toml` reports every problem with its path.
`mytool profile show ...` lists what would run and what would skip, and why.

## 4. Write a case

```python
from cli_framework.runner import Context

from . import REGISTRY


@REGISTRY.case("api.models_listed", needs={"json"}, tags={"smoke"})
def models_listed(ctx: Context) -> None:
    """GET /v1/models lists at least one model."""
    exchange = ctx.state.client(ctx.target).get("/v1/models", case_id=ctx.case_id)
    ctx.require("http_status", exchange.status, 200)
    models = exchange.json().get("data", [])
    ctx.require("model_count", len(models), ">= 1", len(models) >= 1)
```

- `ctx.require(name, observed, expected)` records the check, then fails the case if it does not
  hold. `ctx.check(...)` records without deciding. Record before you decide: the report shows
  the value that caused a failure, and what a pass actually checked.
- `needs={"json"}`: a target that does not declare `json` gets a **skip** that says so, never a
  silent pass.
- Never retry inside a case. A retry hides the failure the case exists to catch.

## 5. Prove the case can fail

A case that has never failed has not been tested. Add the defect to `tests/fake_server.py`
(for example, make `/v1/models` return an empty list) and assert the case fails against it.
The example's `test_each_case_fails_against_its_defect` shows the pattern.

```sh
.venv/bin/python -m pytest tests -q
GATEWAY_URL=https://... GATEWAY_TOKEN=... .venv/bin/mytool run --profile profiles/my-service.toml
```

## Next

- [Usage](usage.md): every command and flag, the run directory, publishing and comparing runs.
- [Deploying](deploying.md): run it nightly on Kubernetes or in CI.
- [Extending](extending.md): notify your own channel, store results anywhere.
