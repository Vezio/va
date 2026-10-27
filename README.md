# cli-framework

[![Tests](https://github.com/Vezio/cli-framework/actions/workflows/offline-tests.yaml/badge.svg)](https://github.com/Vezio/cli-framework/actions/workflows/offline-tests.yaml)
[![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](pyproject.toml)

**A Python framework for building command-line tools that test deployed systems.** It was built
for model-serving stacks (engines, routers and gateways, and the rollouts that change them) and
fits any service you reach over the network. You write the checks; the framework provides
everything a test tool needs around them: profiles, evidence, exit codes, reports, publishing,
comparison between runs, notifications and unattended runs.

Built by Tyler Vezio Rimaldi ([@Vezio](https://github.com/Vezio)) and Alberto Perdomo
([@albertoperdomo2](https://github.com/albertoperdomo2)).

## See it work

```sh
git clone https://github.com/Vezio/cli-framework && cd cli-framework
demo/local/run.sh
```

You need Python 3.11 or newer, and nothing else: no service, no network, no account. The demo
starts a small OpenAI-compatible serving stack on your machine (an engine, and a gateway in
front of it) and checks it with `modelcheck`, an example tool built on the framework. It runs
twice:

1. **Last night**: the stack is healthy; the run passes and is saved as the baseline.
2. **Today**: the gateway has a bug (its streams no longer end with `data: [DONE]`). The same
   cases now fail on the gateway, but not on the engine behind it, which points at the layer
   with the bug, and the comparison with last night calls it a regression:

```
=== What the second run found

  case                             gateway   engine
  auth.rejects_bad_key             pass      skip
  chat.completion[user_only]       pass      pass
  chat.completion[with_system]     pass      pass
  chat.latency                     pass      pass
  chat.usage_adds_up               pass      pass
  errors.unknown_model             pass      pass
  models.lists_declared_model      pass      pass
  stream.well_formed               FAIL      pass

  compared with last night:
  regression: gateway stream.well_formed: pass -> fail
```

The engine's `skip` is deliberate: its profile does not declare authentication, so the run
says so instead of reporting a pass. Each run also leaves an HTML report and a published copy
in `demo/local/out/`. A second demo deploys a tool as a Kubernetes CronJob on a local kind
cluster: [demo/README.md](demo/README.md).

## What you get

| | |
|---|---|
| **Profiles** | describe each target and what a correct one does, with every problem reported at once ([Profiles](docs/profiles.md)) |
| **Evidence for every verdict** | each case records what it observed before it decides: a failure shows the value that caused it |
| **Exit codes that assign blame** | failing cases, a broken environment, a broken tool and failed delivery each exit differently, so CI can tell them apart ([exit codes](docs/usage.md#exit-codes)) |
| **Secrets kept out** | registered credentials and bearer tokens are scrubbed from logs, run files, reports and notifications |
| **Runs you can compare** | every run is a directory, published to disk or S3; regressions, recoveries and flaky cases against earlier runs |
| **Unattended runs** | a `job` command that publishes and notifies even when the run crashes; a Kubernetes CronJob; CI workflows |
| **Plugins** | notifications (Slack, any webhook, any command, or your own), storage and profile formats are chosen by name, and extended with one class |

## Build your own tool

```sh
scripts/new-app.sh mytool ../mytool
```

This creates a working tool to adapt: cases, a profile schema, tests against a fake server,
a container image definition, Kubernetes manifests and a CI workflow. A case records what it
saw, then decides:

```python
@REGISTRY.case("health.status", needs={"health"})
def health_status(ctx: Context) -> None:
    """The health path answers 200."""
    t = ctx.target
    exchange = ctx.state.client(t).get(t.health_path, case_id=ctx.case_id)
    ctx.require("http_status", exchange.status, 200)
```

You write the `run` command (a few lines that build a `Session`, as in the examples); `job`,
`publish`, `compare` and `notify` come with the framework. [Getting started](docs/getting-started.md)
walks through it.

## Use it in your project

The framework is not on PyPI. Depend on a specific commit, so every install and image build gets
the same code:

```toml
dependencies = [
  "cli-framework[s3,yaml] @ https://github.com/Vezio/cli-framework/archive/<commit>.tar.gz",
]
```

`scripts/new-app.sh` writes this for you, pinned to the commit you run it from. The `s3` and
`yaml` extras add publishing to S3 and YAML profiles; without them the framework uses only the
Python standard library.

## Documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | your first tool, step by step |
| [Profiles](docs/profiles.md) | design the profile that describes your targets |
| [Usage](docs/usage.md) | every command and flag, the run directory, exit codes, comparison, notifications |
| [Extending](docs/extending.md) | your own cases, clients, checks, notifiers, storage and commands |
| [Deploying](docs/deploying.md) | run your tool as a Kubernetes CronJob or in CI |
| [Architecture](docs/architecture.md) | how the code is laid out |
| [Design](docs/design.md) | why it works the way it does |

## Repository

```
src/cli_framework/     the framework
examples/modelcheck/   a tool for OpenAI-compatible serving stacks: the demo's tool
examples/httpcheck/    a minimal tool for any HTTP service: the template new-app.sh copies
demo/                  the local demo, and the Kubernetes demo on kind
docs/                  the guides above
tests/                 the framework's own tests
deploy/                a Kubernetes CronJob, RBAC and an example overlay
container/             the runner image and its hash-pinned dependencies
scripts/               install, new-app, lock
.github/workflows/     tests on every change, the kind demo, a scheduled live run
```

## Status

Version 0.3. The API may still change before 1.0; pin a commit, and read
[CHANGELOG.md](CHANGELOG.md) before moving to a newer one. Developing the framework itself:
`scripts/install.sh --dev`, then `.venv/bin/python -m pytest -q`; the checks CI runs are in
[architecture.md](docs/architecture.md#testing).
