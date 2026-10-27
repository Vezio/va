# VA: Verify and Attribute

[![Tests](https://github.com/Vezio/va/actions/workflows/offline-tests.yaml/badge.svg)](https://github.com/Vezio/va/actions/workflows/offline-tests.yaml)
[![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](pyproject.toml)

VA is a Python framework for building command-line tools that verify deployed systems and
attribute every failure to the layer, and the party, responsible. It was designed for
model-serving infrastructure (inference engines, routers and gateways, and the rollouts that
change them) and applies to any service reachable over the network.

A tool built on VA defines its checks; the framework supplies the capabilities every
verification tool needs around them: declarative profiles, recorded evidence, exit codes that
attribute failures, reports, archival and comparison of runs, notifications, and unattended
operation on Kubernetes and in CI.

Built by Tyler **V**ezio Rimaldi ([@Vezio](https://github.com/Vezio)) and **A**lberto Perdomo
([@albertoperdomo2](https://github.com/albertoperdomo2)).

## Contents

- [Capabilities](#capabilities)
- [Quick start](#quick-start)
- [Building a tool](#building-a-tool)
- [Installation](#installation)
- [Documentation](#documentation)
- [Repository layout](#repository-layout)
- [Versioning](#versioning)

## Capabilities

| Capability | Description |
|---|---|
| **Declarative profiles** | Each target, how to reach it, what it supports and how a correct one behaves, in a validated file; every problem is reported in a single pass. See [Profiles](docs/profiles.md). |
| **Recorded evidence** | Every check records the observed and expected values before a verdict is reached, so each failure carries the value that caused it. |
| **Failure attribution** | Distinct exit codes separate failing checks, an unprepared environment, a tool defect and failed delivery, so automation can act on each without parsing logs. See [Exit codes](docs/usage.md#exit-codes). |
| **Credential protection** | Registered credentials and bearer tokens are redacted from logs, run files, reports and notifications. |
| **Run archival and comparison** | Every run is a self-contained directory, published to a file system or S3-compatible storage, and compared with earlier runs to identify regressions, recoveries and unstable checks. |
| **Unattended operation** | A `job` command that publishes and notifies even when a run fails abnormally, a Kubernetes CronJob, and CI workflows. |
| **Extensibility** | Notification channels (Slack, webhooks, commands, or custom), storage backends and profile formats are plugins, selected by configuration and implemented as a single class. |

## Quick start

Requirements: Python 3.11 or later. No external service, network access or credentials are
needed.

```sh
git clone https://github.com/Vezio/va && cd va
demo/local/run.sh
```

The demonstration starts a local OpenAI-compatible serving stack, consisting of an engine and a
gateway in front of it, and verifies it with `modelcheck`, an example tool built on the
framework. It performs two runs:

1. **Baseline.** The stack is healthy. All checks pass and the run is archived as the baseline.
2. **Regression.** A defect is introduced in the gateway: its responses no longer terminate
   streams with `data: [DONE]`. The affected check fails on the gateway and passes on the engine
   behind it, isolating the defect to the gateway, and the comparison with the baseline reports
   it as a regression.

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

The engine's authentication check is skipped by design: its profile does not declare
authentication, and the skip records that reason rather than reporting a pass. Each run also
produces an HTML report and an archived copy in `demo/local/out/`.

A second demonstration deploys a tool as a Kubernetes CronJob on a local kind cluster and
verifies its operational behaviour. See [demo/README.md](demo/README.md).

## Building a tool

```sh
scripts/new-app.sh mytool ../mytool
```

This generates a complete, independent project: checks, a profile schema, tests against a
simulated service, a container image definition with locked dependencies, Kubernetes manifests
and a CI workflow.

Each check records its observations, then reaches a verdict:

```python
@REGISTRY.case("health.status", needs={"health"})
def health_status(ctx: Context) -> None:
    """The health path answers 200."""
    t = ctx.target
    exchange = ctx.state.client(t).get(t.health_path, case_id=ctx.case_id)
    ctx.require("http_status", exchange.status, 200)
```

A tool implements its `run` command, typically a few lines that configure a `Session`; the
`job`, `publish`, `compare` and `notify` commands are provided by the framework. See
[Getting started](docs/getting-started.md) for a complete walkthrough.

## Installation

VA is distributed from source and is not published to PyPI. Depend on a specific
commit so that every installation and image build uses identical code:

```toml
dependencies = [
  "va-framework[s3,yaml] @ https://github.com/Vezio/va/archive/<commit>.tar.gz",
]
```

`scripts/new-app.sh` writes this dependency automatically, pinned to the commit from which it is
run. The optional `s3` and `yaml` extras enable S3 publication and YAML profiles; without them,
the framework depends only on the Python standard library and PyYAML.

## Documentation

| Guide | Contents |
|---|---|
| [Getting started](docs/getting-started.md) | Creating, testing and running a first tool |
| [Profiles](docs/profiles.md) | Designing and validating the profile that describes the targets |
| [Extending profiles](docs/extending-profiles.md) | Adding to a profile schema and evolving it safely |
| [Usage](docs/usage.md) | Commands, flags, run directories, exit codes, comparison and notifications |
| [Extending](docs/extending.md) | Checks, protocol clients, preflight checks, hooks, notifiers, storage and commands |
| [Deploying](docs/deploying.md) | Operation as a Kubernetes CronJob and in CI |
| [Architecture](docs/architecture.md) | Code organisation, data model, concurrency and state |
| [Design](docs/design.md) | Design principles and their rationale |
| [Package reference](src/va/README.md) | The `va` package: public API, modules and extension points |

## Repository layout

```
src/va/     the framework
examples/modelcheck/   example tool for OpenAI-compatible serving stacks, used by the demonstration
examples/httpcheck/    minimal example tool for HTTP services, used as the template for new tools
demo/                  local and Kubernetes (kind) demonstrations
docs/                  guides
tests/                 the framework's test suite
deploy/                Kubernetes CronJob, RBAC and example overlay
container/             runner image definition and hash-pinned dependencies
scripts/               installation, project generation and dependency locking
.github/workflows/     continuous integration, the kind demonstration and a scheduled live run
```

## Versioning

The current version is 0.4. Until 1.0, minor versions may change behaviour; such changes are
identified in [CHANGELOG.md](CHANGELOG.md), which should be reviewed before updating a pinned
commit.

To develop the framework itself, run `scripts/install.sh --dev`, then
`.venv/bin/python -m pytest -q`. The complete set of checks run in CI is listed in
[Architecture](docs/architecture.md#testing).
