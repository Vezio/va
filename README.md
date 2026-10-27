# VA: Verify and Attribute

[![Tests](https://github.com/Vezio/va/actions/workflows/offline-tests.yaml/badge.svg)](https://github.com/Vezio/va/actions/workflows/offline-tests.yaml)
[![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

VA is a Python framework for building command-line tools that verify deployed systems and
attribute each failure to the component, and the party, responsible for it. It applies to any
system that can be reached and observed: web services and APIs, gateways and load balancers,
databases and queues, data pipelines, and the infrastructure and releases that change them.

VA was originally developed to verify model-serving infrastructure: inference engines, the
routers and gateways in front of them, and the rollouts that change them. That remains a primary
use case, and the included demonstration is a model-serving example.

A tool built on VA defines what to check. The framework provides what every verification tool
needs around those checks: declarative profiles, recorded evidence, exit codes that attribute
failures, reports, archival and comparison of runs, notifications, and unattended operation on
Kubernetes and in CI.

Built by Tyler **V**ezio Rimaldi ([@Vezio](https://github.com/Vezio)) and **A**lberto Perdomo
([@albertoperdomo2](https://github.com/albertoperdomo2)).

## Capabilities

| Capability | Description |
|---|---|
| **Declarative profiles** | Each target, how to reach it, what it supports and how a correct one behaves, in a validated YAML file; every problem is reported in a single pass. |
| **Layered systems** | Targets declared as the layers of one system (for example a gateway, a router and an engine) are run outermost first, and each failure is attributed to the layer where it starts. |
| **Recorded evidence** | Every check records the observed and expected values before a verdict is reached, and clients record each request and response. |
| **Failure attribution** | Distinct exit codes separate failing checks, an unprepared environment, a tool defect and failed delivery, so automation can act on each without parsing logs. |
| **Credential protection** | Registered credentials and bearer tokens are redacted from logs, run files, reports and notifications. |
| **Run archival and comparison** | Each run is a self-contained directory, published to a file system or S3-compatible storage and compared with earlier runs to report regressions and recoveries. |
| **Unattended operation** | A `job` command that publishes and notifies even when a run fails abnormally, a Kubernetes CronJob, and CI workflows. |
| **Any protocol, any channel** | Checks use a client the tool provides (HTTP in the examples; gRPC, SQL or a queue equally). Notification channels, storage backends and profile formats are plugins. |

Typical uses include post-deployment verification as a release gate, scheduled conformance
checks, comparing staging with production, and API and contract testing, as well as model-serving
infrastructure checked layer by layer before and after each rollout.

## Quick start

Requirements: Python 3.11 or later. No external service, network access or credentials are
needed.

```sh
git clone https://github.com/Vezio/va && cd va
demo/local/run.sh
```

The demonstration starts a simulated model-serving stack on the local machine (an inference
engine, a router in front of it and a gateway in front of the router) and verifies it twice with
the example tool `modelcheck`: once healthy, and once with a defect injected in the router. The
second run fails at the router and the gateway, passes at the engine, attributes the defect to
the router and reports it as a regression:

```
  case                          gateway  router   engine   starts at
  chat.usage_adds_up            FAIL     FAIL     pass     router
```

[demo/README.md](demo/README.md) describes both demonstrations, including one that deploys a tool
as a Kubernetes CronJob on a local kind cluster.

## Building a tool

```sh
scripts/new-app.sh mytool ../mytool
```

This generates an independent project from the minimal HTTP example: checks, a profile schema,
tests against a simulated service, a container image definition with locked dependencies,
Kubernetes manifests and a CI workflow. A check records its observations, then reaches a verdict:

```python
@REGISTRY.case("health.status", needs={"health"})
def health_status(ctx: Context) -> None:
    """Verify that the health endpoint returns HTTP 200."""
    t = ctx.target
    exchange = ctx.state.client(t).get(t.health_path, case_id=ctx.case_id)
    ctx.require("http_status", exchange.status, 200)
```

The tool implements its `run` command; `job`, `publish`, `compare` and `notify` come from the
framework. [Getting started](docs/getting-started.md) walks through a complete tool.

## Installation

VA is distributed from source. Depend on a specific commit so that every installation and image
build uses identical code:

```toml
dependencies = [
  "va-framework[s3] @ https://github.com/Vezio/va/archive/<commit>.tar.gz",
]
```

`scripts/new-app.sh` writes this dependency, pinned to the commit it is run from. The framework
requires only the Python standard library and PyYAML; the `s3` extra adds publication to S3 and
S3-compatible storage. Until 1.0, a minor version may change behaviour, so review the commit
history before moving a pin.

## Documentation

| Guide | Contents |
|---|---|
| [Getting started](docs/getting-started.md) | Creating, testing and running a first tool |
| [Writing and registering cases](docs/cases.md) | How cases are registered, loaded, selected and run |
| [Profiles](docs/profiles.md) | Designing, validating and extending the profile that describes the targets |
| [Usage](docs/usage.md) | Commands, flags, exit codes, run directories, comparison and notifications |
| [Extending](docs/extending.md) | Protocol clients, layer kinds, preflight checks, hooks, notifiers, storage and commands |
| [Deploying](docs/deploying.md) | Operation as a Kubernetes CronJob and in CI |
| [Architecture](docs/architecture.md) | For framework developers: package layout, design rules and development workflow |

## Repository layout

```
src/va/                the framework
examples/httpcheck/    minimal example tool for HTTP services, used as the template for new tools
examples/modelcheck/   example tool for model-serving stacks, used by the local demonstration
demo/                  local and Kubernetes (kind) demonstrations
docs/                  guides
tests/                 the framework's test suite
deploy/                Kubernetes CronJob, RBAC and example overlay
container/             runner image definition and hash-locked dependencies
scripts/               installation, project generation and dependency locking
```

To develop the framework itself, run `scripts/install.sh --dev`, then `.venv/bin/python -m pytest
-q`; [Architecture](docs/architecture.md#development) lists every check CI runs.

## License

Licensed under the [Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE).
