# cli-framework

[![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](pyproject.toml)

**A Python framework for building CLIs that test deployed systems**: model-serving stacks
(engines, routers, gateways) first, any HTTP service in general. You write the checks; the
framework handles everything around them.

Built by Tyler Vezio Rimaldi ([@Vezio](https://github.com/Vezio)) and Alberto Perdomo
([@albertoperdomo2](https://github.com/albertoperdomo2)).

```
$ demo/local/run.sh
run 2026-10-27T01-14-41Z-c75f: 5 case(s) x 1 target(s)
  PASS  httpbin      api.json_content_type
  PASS  httpbin      api.unknown_path[GET]
  PASS  httpbin      api.unknown_path[POST]
  SKIP  httpbin      auth.rejects_bad_key
  PASS  httpbin      health.latency
  PASS  httpbin      health.status
httpcheck demo: PASSED (exit 0)
5 passed, 0 failed, 0 errors, 1 skipped (1 undeclared)
```

## Why

Testing live infrastructure goes wrong in the same ways every time: a dead tunnel reported as a
failing service, a retry that hides the bug, a pass that checked nothing, a key in a published
log. This framework encodes the fixes once, so each new tool starts right:

| | |
|---|---|
| **Failures say whose they are** | the target, your environment, the tool, or delivery: CI can tell them apart without reading logs ([exit codes](docs/usage.md#exit-codes)) |
| **Every verdict has evidence** | checks are recorded before deciding; every request and response is kept |
| **Secrets never leak** | redacted in logs, files, reports and notifications |
| **Runs are archived and compared** | published to `file://` or `s3://`; regressions, recoveries and flaky cases called out |
| **Unattended by design** | a `job` command that survives its own crash, a Kubernetes CronJob, CI workflows |
| **Pluggable** | notify Slack, any webhook or a command; store anywhere; add your own as one class |

## Try it

```sh
git clone https://github.com/Vezio/cli-framework && cd cli-framework
demo/local/run.sh        # checks a public test service; writes an HTML report
demo/kind/up.sh          # the same tool as a Kubernetes CronJob, on a local kind cluster
```

What each demo shows: [demo/README.md](demo/README.md).

## Build your own tool

```sh
scripts/new-app.sh mytool ../mytool      # a working copy of the example, renamed
```

A case is a function that records what it saw, then decides:

```python
@REGISTRY.case("health.status", needs={"health"})
def health_status(ctx):
    response = ctx.state.client(ctx.target).get(ctx.target.health_path)
    ctx.require("http_status", response.status, 200)
```

Your app gets `run`, `job`, `publish`, `compare` and `notify` commands for free.
[Getting started](docs/getting-started.md) walks through it.

## Documentation

| | |
|---|---|
| [Getting started](docs/getting-started.md) | your first tool, step by step |
| [Usage](docs/usage.md) | commands, flags, the run directory, exit codes, notifications |
| [Extending](docs/extending.md) | notifiers, storage, profile formats, hooks, commands |
| [Deploying](docs/deploying.md) | Kubernetes CronJob, on-demand runs, CI, the runner image |
| [Architecture](docs/architecture.md) | how the code is laid out |
| [Design](docs/design.md) | why it works the way it does |

## Repository

```
src/cli_framework/   the framework
examples/httpcheck/  a complete tool built on it: start here
demo/                run it locally, or on a kind cluster as a CronJob
tests/               the framework's own tests
docs/                the guides above
deploy/              Kubernetes CronJob, RBAC and an example overlay
container/           the runner image and its hash-pinned dependencies
scripts/             install, new-app, lock
```
