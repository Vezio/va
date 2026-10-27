# Extending

What you change to make the framework yours, from the most common to the least. Your cases,
profile, client and checks live in your tool. Notifiers, storage and profile formats are
plugins: one class or function, registered by name, chosen at run time; nothing in the framework
changes.

| To add | Where | Chosen by |
|---|---|---|
| [cases](cases.md) | your `cases/` package and `registry.py` | `-k`, `--tag` |
| [a profile schema](profiles.md), [or extend one](profiles.md#extending-a-schema) | your `profile.py` | `--profile` |
| [a client for another protocol](#a-client-for-another-protocol) | your `client.py`, your `State` | the cases |
| [a layer kind](#a-layer-kind) | your `LayerKinds` registry | `kind:` on a target |
| [preflight checks](#preflight-checks) | your `run` command | always run first |
| [behaviour during a run](#hooks) | a `RunHooks` subclass | your `run` command |
| [a notification channel](#a-notification-channel) | `PLUGINS.notifiers` | `--notify KIND:TARGET` |
| [a results store](#a-results-store) | `PLUGINS.sinks` | the scheme of `--remote-output` |
| [a profile format](#a-profile-format) | `PLUGINS.profile_loaders` | the profile's file suffix |
| [a command](#a-command) | `app.add(...)` | the command line |
| [skip reasons](#skip-reasons) | `App(skip_classes=...)` | `ctx.skip(...)` |
| [a setting](#settings) | `app.settings` | `MYTOOL_<KEY>` |

## A client for another protocol

The examples speak HTTP through `client.py`, a small client that records every request and
response into the run's `exchanges.jsonl`. Testing something else (gRPC, a database, a queue) is
a different client on your `State`; cases call `ctx.state.client(ctx.target)` as before. Record
each exchange through the run directory, which scrubs secrets on the way:

```python
import time

from va import RunDir


class RecordingDbClient:
    """Runs queries against one target and keeps each one as evidence."""

    def __init__(self, run: RunDir, connection):
        self.run = run
        self.connection = connection

    def query(self, sql: str, *, case_id: str) -> list[tuple]:
        started = time.monotonic()
        error, rows = None, []
        try:
            rows = self.connection.execute(sql).fetchall()
        except Exception as exc:  # recorded, then re-raised: the case decides
            error = str(exc)
            raise
        finally:
            self.run.append_jsonl("exchanges.jsonl", [{
                "case_id": case_id, "sql": sql, "rows": len(rows), "error": error,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            }])
        return rows
```

Do not retry requests in a client used by cases; a retry could turn an intermittent failure in
the target into a pass.

## A layer kind

When targets are the layers of one stack ([Profiles: Layers](profiles.md#layers)), your tool owns
the vocabulary of kinds: one `LayerKinds` registry, usually in its own module. A kind is a name,
a depth (0 is the outermost: what a client calls first) and, when calling it differs, an
adapter. From [`examples/modelcheck/src/modelcheck/layers.py`](../examples/modelcheck/src/modelcheck/layers.py):

```python
from va import AnswersItself, HeaderServedBy, LayerKinds

KINDS = LayerKinds()
KINDS.register("proxy", depth=0, adapter=HeaderServedBy("x-served-by"))
KINDS.register("router", depth=1, adapter=HeaderServedBy("x-served-by"))
KINDS.register("engine", depth=2, adapter=AnswersItself())
```

A new kind is one more `register` call: profiles can use it at once, and it is ordered and
validated with the others. Kinds of equal depth are allowed in front of each other (a proxy behind
a proxy); a kind of greater depth in front of a lesser one is refused.

An adapter is the one place for what differs about calling a kind of layer, so cases never
branch on it. Subclass `LayerAdapter` and override only what differs:

| Method | Default | Override when |
|---|---|---|
| `request_headers(layer)` | none | the layer needs a header on every request: to route to one backend, or to select a pool |
| `served_by(layer, headers)` | `None` | the layer says which component answered; recorded with each exchange |

```python
class PoolRouter(LayerAdapter):
    """A router that picks a pool from a header, and names the endpoint it chose."""

    def request_headers(self, layer):
        return {"x-target-pool": layer.pool}            # a field your profile declares

    def served_by(self, layer, headers):
        return headers.get("x-endpoint")
```

Your client applies them: it is built with the target and `KINDS.adapter(target)`, merges
`request_headers` into every request, and records `served_by` with each exchange (see
modelcheck's `client.py`). `served_by` is evidence, never a verdict on its own.

To place failures, add the `LayerLocator` hook to the run with the ordered targets:

```python
hooks=[*options.hooks(), LayerLocator(profile.targets)]
```

It writes `layers.json` (the stack as declared, and where each failing case's failure starts) and
logs one line per failure. `LayerKinds.locate(layers, verdicts)` is the same rule for one case,
for a report or dashboard of your own.

## Preflight checks

A check is a function that returns problems; preflight runs every check before any case, and a
problem of severity `ERROR` stops the run with exit 3, saying whose fix it is. A `WARNING` is
reported and the run goes on. The framework has `env_set`, `reachable` and
`destination_writable`; add your own to the list your `run` command builds:

```python
from va import Problem, Severity


def model_is_served(name: str, url: str, model: str):
    def check() -> list[Problem]:
        import json, urllib.request
        with urllib.request.urlopen(f"{url}/v1/models", timeout=10) as response:
            ids = [m["id"] for m in json.load(response)["data"]]
        if model not in ids:
            return [Problem("deployment", f"{name} does not serve {model}; it serves {ids}")]
        return []

    return check
```

Use preflight for what makes every case meaningless (nothing to test against), never for
what a case should judge.

## Hooks

Hooks observe a run: stream results somewhere, emit metrics, write an extra file into the run.
A hook that raises is logged and ignored, so a broken hook never changes a verdict.

```python
from va import RunHooks


class Metrics(RunHooks):
    def on_result(self, run, record):
        CASES.labels(verdict=record.verdict.value).inc()   # e.g. a Prometheus counter

    def on_finish(self, run, outcome):
        run.write_text("extra-report.md", "...")          # files written here are published
```

The methods are `on_start`, `on_result`, `on_finish` (before publication) and `on_published`.
Add yours to the hooks your `run` command passes to the `Session`, after the ones the flags ask
for:

```python
        hooks=[*options.hooks(), Metrics()],
```

The HTML, JUnit and metrics outputs are hooks too (`va.outputs`).

## A notification channel

A notifier has a `name`, is built from the text after the colon in its spec, and has
`send(report)`. This one pages through PagerDuty's Events API v2 when a run fails, and resolves
the page when a later run passes:

```python
from va.notifiers import post_json
from va.plugins import PLUGINS
from va.report import RunReport


@PLUGINS.notifiers.register("pagerduty")
class PagerDutyNotifier:
    name = "pagerduty"

    def __init__(self, target: str):
        self.routing_key = target  # resolved and redacted when the spec says env:NAME

    def send(self, report: RunReport) -> None:
        post_json("https://events.pagerduty.com/v2/enqueue", {
            "routing_key": self.routing_key,
            "event_action": "resolve" if report.ok else "trigger",
            "dedup_key": f"{report.tool}:{report.subject}",  # one incident per tool and system
            "payload": {
                "summary": f"{report.title()}: {report.counts_line()}",
                "source": report.subject,
                "severity": "error",
            },
        })
```

```sh
mytool run --profile p.yaml --notify pagerduty:env:PAGERDUTY_ROUTING_KEY
```

`post_json` retries network errors, 429 and 5xx within bounds. The `RunReport` has everything a
message needs: `title()`, `counts_line()`, `ok`, `headline`, `subject`, `failures`,
`comparison`, `links`, `markdown()`, and `to_dict()` for all of it.

## A results store

A sink stores files under keys. Give it a `uri` attribute and six methods, and register its
class for a URI scheme:

```python
from pathlib import Path

from va.plugins import PLUGINS


@PLUGINS.sinks.register("gs")
class GcsSink:
    def __init__(self, uri: str):                        # gs://bucket/prefix
        self.uri = uri
    def unavailable_reason(self) -> str | None: ...       # e.g. "no credentials"; None if usable
    def write_check(self) -> None: ...                    # prove a write works, before the run
    def put(self, local: Path, key: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def list(self, prefix: str) -> list[str]: ...         # every key under prefix, sorted
    def location(self, key: str) -> str: ...              # a URI a person can follow
```

Publishing (manifest last), comparison, `job` and preflight then work with `gs://` unchanged.
When `unavailable_reason` returns a reason, publication is skipped with a warning instead of
failing the run.

## A profile format

YAML (the default), TOML and JSON are built in. Another is a function from the file's text to a mapping,
registered for a suffix:

```python
import json5

from va.plugins import PLUGINS

PLUGINS.profile_loaders.register("json5", json5.loads)
```

## A command

```python
from va import App, ExitCode


class ReplayCommand:
    name, help, passthrough = "replay", "re-send a recorded request", False

    def add_arguments(self, parser, app):
        parser.add_argument("run_dir", help="the run to replay from")

    def run(self, args, app) -> int:
        ...
        return ExitCode.OK


app = App("mytool", version="1.0")  # in your build_app()
app.add(ReplayCommand())
```

Raise `UsageError` for a usage problem (exit 2); any other exception is reported as an internal
error (exit 4), never as a test failure. Set `passthrough = True` to receive unknown arguments
as `args.passthrough`, and add a `description` attribute for a longer `--help` text.

## Skip reasons

A skip says why a case did not run. Add a reason when none of the built-in ones fit, and never
reuse one for a different reason:

```python
from va import App, SkipClass, SkipClasses

app = App("mytool", version="1.0", skip_classes=SkipClasses.default().extend(
    SkipClass("requires_gpu", "needs a GPU node this cluster does not have"),
))
```

A case then calls `ctx.skip("requires_gpu", "no GPU node in this cluster")`. A skip with a reason
the tool does not know is recorded as an `error`, so a typo cannot hide a case.

## Settings

`app.settings.get("REGION")` reads `MYTOOL_REGION`; `get_float`, `get_int` and `get_list` parse
it. Use it as a flag's default, so the flag and the variable are one setting.

## Shipping a plugin as a package

A plugin can live in its own package and be found without being imported, through an entry
point. In the plugin's `pyproject.toml`:

```toml
[project.entry-points."va.notifiers"]
pagerduty = "mytool_pagerduty:PagerDutyNotifier"

[project.entry-points."va.sinks"]
gs = "mytool_gcs:GcsSink"
```

Install it next to your tool, and `--notify pagerduty:...` works. The groups are
`va.notifiers`, `va.sinks` and `va.profile_loaders`. A name that
is already registered is not replaced.
