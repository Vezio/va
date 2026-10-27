# VA: Verify and Attribute

[![Tests](https://github.com/Vezio/va/actions/workflows/offline-tests.yaml/badge.svg)](https://github.com/Vezio/va/actions/workflows/offline-tests.yaml)
[![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](pyproject.toml)

VA is a Python framework for building command-line tools that verify deployed systems and
attribute each failure to the component, and the party, responsible for it. It applies to any
system that can be reached and observed: web services and APIs, gateways and load balancers,
databases and queues, data pipelines, and the infrastructure and releases that change them.

A tool built on VA defines what to check. The framework provides what every verification tool
needs around those checks: declarative profiles, recorded evidence, exit codes that attribute
failures, reports, archival and comparison of runs, notifications, and unattended operation on
Kubernetes and in CI.

Built by Tyler **V**ezio Rimaldi ([@Vezio](https://github.com/Vezio)) and **A**lberto Perdomo
([@albertoperdomo2](https://github.com/albertoperdomo2)).

## Contents

- [Capabilities](#capabilities)
- [Use cases](#use-cases)
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
| **Layered systems** | Targets declared as the layers of one system, such as a load balancer, an API gateway and the service behind it, validated as a stack and run outermost first. Each failure is attributed to the layer where it starts: the layer that fails while the layer behind it passes. See [Layers](docs/profiles.md#layers). |
| **Recorded evidence** | Every check records the observed and expected values before a verdict is reached, so each failure carries the value that caused it. |
| **Failure attribution** | Distinct exit codes separate failing checks, an unprepared environment, a tool defect and failed delivery, so automation can act on each without parsing logs. See [Exit codes](docs/usage.md#exit-codes). |
| **Credential protection** | Registered credentials and bearer tokens are redacted from logs, run files, reports and notifications. |
| **Run archival and comparison** | Every run is a self-contained directory, published to a file system or S3-compatible storage, and compared with earlier runs to identify regressions, recoveries and unstable checks. |
| **Unattended operation** | A `job` command that publishes and notifies even when a run fails abnormally, a Kubernetes CronJob, and CI workflows. |
| **Any protocol** | Checks call the system through a client the tool provides: HTTP in the examples, and equally gRPC, SQL, a message queue or a command-line interface. Each exchange is recorded as evidence. |
| **Extensibility** | Notification channels (Slack, webhooks, commands, or custom), storage backends and profile formats are plugins, selected by configuration and implemented as a single class. |

## Use cases

| Use case | How VA supports it |
|---|---|
| Post-deployment verification | Run after a rollout, triggered from a pipeline or a Kubernetes rollout hook; the exit code gates promotion. |
| Scheduled conformance checks | Run nightly as a CronJob or CI workflow; each run is archived and compared with earlier runs to report regressions and recoveries. |
| Release and environment comparison | Run the same checks against staging and production, or against two versions, with one profile per environment. |
| Layered systems | Check every layer of a request path and identify the layer where a defect starts, rather than only observing it at the edge. |
| API and contract testing | Verify that services answer the interfaces they declare, including error responses, against declarations held in a profile. |
| Operational readiness | Separate failures of the system under test from problems with credentials, connectivity or the test environment, so the right team is notified. |

## Quick start

Requirements: Python 3.11 or later. No external service, network access or credentials are
needed.

```sh
git clone https://github.com/Vezio/va && cd va
demo/local/run.sh
```

The demonstration starts a simulated three-layer service on the local machine (a gateway, a
router behind it and a backend behind the router) and verifies it with `modelcheck`, one of the
example tools. The example happens to use an OpenAI-compatible inference API; the same approach
applies to any layered service. It performs two runs:

1. **Baseline.** The stack is healthy. All checks pass and the run is archived as the baseline.
2. **Regression.** A defect is introduced in the router, which now removes a field from the
   responses it forwards. The affected check fails at the router and at the gateway in front of
   it, and passes at the backend behind them. The run attributes the defect to the router, and
   the comparison with the baseline reports the regression.

```
=== What the second run found

  case                          gateway  router   engine   starts at
  auth.rejects_bad_key          pass     skip     skip
  chat.completion[user_only]    pass     pass     pass
  chat.completion[with_system]  pass     pass     pass
  chat.latency                  pass     pass     pass
  chat.usage_adds_up            FAIL     FAIL     pass     router
  errors.unknown_model          pass     pass     pass
  models.lists_declared_model   pass     pass     pass
  stream.well_formed            pass     pass     pass

  compared with last night:
  regression: gateway chat.usage_adds_up: pass -> fail
  regression: router chat.usage_adds_up: pass -> fail
```

The authentication check is skipped behind the gateway by design: the router and backend do not
declare authentication, and the skip records that reason rather than reporting a pass. Each run
also produces an HTML report and an archived copy in `demo/local/out/`.

A second demonstration deploys a tool as a Kubernetes CronJob on a local kind cluster and
verifies its operational behaviour. See [demo/README.md](demo/README.md).

## Building a tool

```sh
scripts/new-app.sh mytool ../mytool
```

This generates a complete, independent project from the minimal HTTP example: checks, a profile
schema, tests against a simulated service, a container image definition with locked
dependencies, Kubernetes manifests and a CI workflow. Replace the HTTP client and checks with
those for the system being verified.

Each check records its observations, then reaches a verdict:

```python
@REGISTRY.case("health.status", needs={"health"})
def health_status(ctx: Context) -> None:
    """Verify that the health endpoint returns HTTP 200."""
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
run. The framework requires only the Python standard library and PyYAML. The optional `s3` extra
adds publication to S3 and S3-compatible storage; the `yaml` extra is retained for compatibility
and adds nothing.

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
src/va/                the framework
examples/httpcheck/    minimal example tool for HTTP services, used as the template for new tools
examples/modelcheck/   example tool for a layered service (an inference API), used by the demonstration
demo/                  local and Kubernetes (kind) demonstrations
docs/                  guides
tests/                 the framework's test suite
deploy/                Kubernetes CronJob, RBAC and example overlay
container/             runner image definition and hash-pinned dependencies
scripts/               installation, project generation and dependency locking
.github/workflows/     continuous integration, the kind demonstration and a scheduled live run
```

## Versioning

The current version is 0.5. Until 1.0, minor versions may change behaviour; such changes are
identified in [CHANGELOG.md](CHANGELOG.md), which should be reviewed before updating a pinned
commit.

To develop the framework itself, run `scripts/install.sh --dev`, then
`.venv/bin/python -m pytest -q`. The complete set of checks run in CI is listed in
[Architecture](docs/architecture.md#testing).
