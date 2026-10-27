# Demonstrations

Two demonstrations show cli-framework in operation: a local demonstration that runs in a few
seconds, and a Kubernetes demonstration that deploys a tool to a disposable cluster and verifies
its operational behaviour.

| Demonstration | Tool | Requirements | Duration |
|---|---|---|---|
| [Local](#local-demonstration) | `modelcheck` | Python 3.11 or later | a few seconds |
| [Kubernetes](#kubernetes-demonstration) | `httpcheck` | Python 3.11 or later, kind, kubectl, and Docker or Podman | about three minutes |

## Local demonstration

```sh
demo/local/run.sh
```

### Overview

The script requires no external service, network access or credentials. It starts a simulated
OpenAI-compatible serving stack on free local ports: an **engine**, and a **gateway** in front
of it that authenticates requests and forwards them. It then verifies the stack with
[`modelcheck`](../examples/modelcheck).

The profile, [`stack.yaml`](../examples/modelcheck/profiles/stack.yaml), declares both layers and
the behaviour expected of each: the model served, the status returned for an unknown model, a
latency budget per layer, and that only the gateway requires an API key. The following checks
run against each layer:

| Check | Verifies that the layer |
|---|---|
| `models.lists_declared_model` | lists the declared model |
| `chat.completion[user_only]`, `[with_system]` | returns content and a valid finish reason, for two prompt structures |
| `chat.usage_adds_up` | reports consistent token usage |
| `chat.latency` | responds within its declared latency budget; runs exclusively, never concurrently with other checks |
| `stream.well_formed` | streams valid chunks with a single finish reason, terminated by `data: [DONE]` |
| `errors.unknown_model` | rejects an unknown model with its declared status, in a JSON error envelope |
| `auth.rejects_bad_key` | rejects an invalid key; runs on the gateway and is skipped on the engine, which does not declare authentication |

### Procedure

The script performs two runs:

1. **Baseline.** The stack is healthy. All checks pass, the engine's authentication check is
   skipped with its reason recorded, and the run is archived to `demo/local/out/archive/` as the
   nightly baseline.
2. **Regression.** The stack restarts with a gateway defect: streamed responses no longer end with
   `data: [DONE]`, which leaves clients waiting indefinitely. `stream.well_formed` fails on the
   gateway and passes on the engine, isolating the defect to the gateway, and the comparison
   with the baseline reports it as the single regression.

### Expected results

```
  case                             gateway   engine
  ...
  stream.well_formed               FAIL      pass

  compared with last night:
  regression: gateway stream.well_formed: pass -> fail
```

The script prints the location of each run's HTML report, which lists every check with its
recorded observations. `demo/local/out/` contains both run directories and the archive. The
script exits with status 0 when the demonstration behaves as described, and runs in CI on every
change.

### Further scenarios

The simulated stack supports additional defects, each covered by the `modelcheck` test suite
(`examples/modelcheck/tests/`). Any of them can be started manually:

```sh
.venv/bin/python examples/modelcheck/tests/fake_stack.py --defect gateway_error_status
GATEWAY_URL=http://127.0.0.1:8400 ENGINE_URL=http://127.0.0.1:8401 GATEWAY_TOKEN=demo-gateway-key \
  .venv/bin/modelcheck run --profile examples/modelcheck/profiles/stack.yaml
```

## Kubernetes demonstration

```sh
demo/kind/up.sh       # create the cluster, deploy, and perform a run (about one minute)
demo/kind/test.sh     # verify the operational behaviours (about two minutes)
demo/kind/down.sh     # delete the cluster
```

### Overview

This demonstration deploys `httpcheck`, the minimal example tool, as it would run in production.
`up.sh` creates a local kind cluster, builds the runner image, and deploys
[`deploy/base`](../deploy/base) without modification, together with a demonstration overlay that
adds:

- `target`: an HTTP service under test (go-httpbin);
- `s3`: an S3-compatible object store for run archives (moto in server mode), and a Job that
  creates the bucket;
- the profile, settings and demonstration credentials.

`up.sh` then starts a run as a deployment pipeline would, with `deploy/trigger.sh`, waits for it
to complete, and reads the archived run back from the object store:

```
==> start a run, as a rollout would (deploy/trigger.sh)
httpcheck: ok (exit 0) -> /work/2026-10-27T01-33-15-412Z-3be0
published to s3://results/nightly/demo/2026-10-27T01-33-15-412Z-3be0/
==> read the published run back from the bucket
s3://results/nightly/demo/2026-10-27T01-33-15-412Z-3be0/: ok, 5 passed, 0 failed, 0 errors, 1 skipped
```

### Verified behaviours

`test.sh` verifies the behaviours that matter in unattended operation:

| Scenario | Expected behaviour |
|---|---|
| Scheduled execution | The CronJob runs on schedule, and each scheduled run is compared with the previous one. |
| The target returns incorrect responses | The run exits with status 1; the Job fails **without retrying**; the evidence is archived. |
| The target is unavailable | The run exits with status 3 (environment), not 1 (target); the evidence is archived. |

The same verification runs in CI (`kind-demo.yaml`).

### Isolation

The demonstration uses a dedicated kubeconfig, `demo/kind/.kubeconfig`, and every `kubectl`
command is bound to the demonstration cluster. The scripts never read or modify other clusters
or contexts.

| File | Purpose |
|---|---|
| [`kind/up.sh`](kind/up.sh), [`kind/test.sh`](kind/test.sh), [`kind/down.sh`](kind/down.sh) | deployment and run; verification; removal |
| [`kind/lib.sh`](kind/lib.sh) | shared functions; every `kubectl` call is bound to the demonstration cluster |
| [`kind/overlay/`](kind/overlay) | the demonstration overlay applied to `deploy/base` |
| [`kind/cluster.yaml`](kind/cluster.yaml) | the single-node cluster configuration |
