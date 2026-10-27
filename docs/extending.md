# Extending

Everything an organisation will want to swap is a plugin chosen by name at run time. A plugin
is one class or function, registered in code or shipped as its own package. Nothing in the
framework changes.

| To add | Register in | Chosen by |
|---|---|---|
| a notification channel | `PLUGINS.notifiers` | `--notify KIND:TARGET` |
| a results store | `PLUGINS.sinks` | the URI scheme of `--remote-output` |
| a profile format | `PLUGINS.profile_loaders` | the profile's file suffix |
| behaviour during a run | a `RunHooks` subclass | passed to `Session(hooks=...)` |
| a command | `app.add(...)` | the command line |

## A notification channel

A notifier has a `name` and a `send(report)` method, and is built from the text after the
colon in its spec. This one posts to Microsoft Teams:

```python
from cli_framework.notifiers import post_json
from cli_framework.plugins import PLUGINS
from cli_framework.report import RunReport


@PLUGINS.notifiers.register("teams")
class TeamsNotifier:
    name = "teams"

    def __init__(self, target: str):
        self.url = target  # already resolved when the spec says env:NAME

    def send(self, report: RunReport) -> None:
        post_json(self.url, {"text": f"{report.title()}\n{report.counts_line()}"})
```

```sh
mytool run --profile p.toml --notify teams:env:TEAMS_WEBHOOK
```

`post_json` retries network errors, 429 and 5xx within bounds. The `RunReport` carries
everything: `title()`, `counts_line()`, `failures`, `comparison`, `links`, `markdown()`, and
`to_dict()` for the full data.

## A results store

Implement six methods and register the class for a URI scheme:

```python
from pathlib import Path

from cli_framework.plugins import PLUGINS


@PLUGINS.sinks.register("gs")
class GcsSink:
    def __init__(self, uri: str): ...           # gs://bucket/prefix
    def unavailable_reason(self) -> str | None: ...  # e.g. "no credentials"; None if usable
    def write_check(self) -> None: ...          # prove a write works, before the run
    def put(self, local: Path, key: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def list(self, prefix: str) -> list[str]: ...
    def location(self, key: str) -> str: ...    # a URI a person can follow
```

Publishing, the manifest-last rule, comparison and `job` then work with `gs://` unchanged.

## A profile format

```python
from cli_framework.plugins import PLUGINS
import json5

PLUGINS.profile_loaders.register("json5", json5.loads)
```

## Behaviour during a run

Hooks observe a run without changing it; a hook that raises is logged and ignored.

```python
from cli_framework import RunHooks


class Metrics(RunHooks):
    def on_result(self, run, record):
        CASES.labels(verdict=record.verdict).inc()   # e.g. a Prometheus counter

    def on_finish(self, run, outcome):
        run.write_text("extra-report.md", "...")      # files written here are published
```

The methods are `on_start`, `on_result`, `on_finish` and `on_published`. The built-in HTML,
JUnit and metrics outputs are hooks too (`cli_framework.outputs`).

## A command

```python
from cli_framework import App


class ReplayCommand:
    name, help, passthrough = "replay", "re-send a recorded request", False

    def add_arguments(self, parser, app):
        parser.add_argument("run_dir")

    def run(self, args, app) -> int:
        ...
        return 0  # an ExitCode


app = App("mytool", version="1.0")  # in your build_app()
app.add(ReplayCommand())
```

Raise `UsageError` for a usage problem (exit 2); any other exception is reported as an
internal error (exit 4), never as a test failure.

## Skip classes

Add a reason a case may be skipped; never reuse one for a different reason.

```python
from cli_framework import App, SkipClass, SkipClasses

app = App("mytool", version="1.0", skip_classes=SkipClasses.default().extend(
    SkipClass("requires_gpu", "needs a GPU node this cluster does not have"),
))
```

## Shipping a plugin as a package

Register through an entry point and it is found without any import:

```toml
# pyproject.toml of your plugin package
[project.entry-points."cli_framework.notifiers"]
teams = "mytool_teams:TeamsNotifier"

[project.entry-points."cli_framework.sinks"]
gs = "mytool_gcs:GcsSink"
```

The groups are `cli_framework.notifiers`, `cli_framework.sinks` and
`cli_framework.profile_loaders`.
