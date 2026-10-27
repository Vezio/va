# Demos

Two ways to see cli-framework work: on your laptop in a few seconds, and on a throwaway
Kubernetes cluster that runs a tool the way production would.

## Local: find a bug, and the layer it is in

```sh
demo/local/run.sh
```

Needs only Python 3.11 or newer: no service, no network, no account. The script starts a small
OpenAI-compatible serving stack on free local ports (an **engine**, and a **gateway** in front of
it that checks an API key and forwards requests, like a real stack) and checks it with
[`modelcheck`](../examples/modelcheck), the example tool built on the framework.

The profile, [`stack.toml`](../examples/modelcheck/profiles/stack.toml), declares both layers and
what a correct one does: the model it serves, the status for an unknown model, a latency budget
per layer, and that only the gateway requires a key. Seven cases then run against each layer:

| Case | Checks that the layer... |
|---|---|
| `models.lists_declared_model` | lists the declared model |
| `chat.completion[user_only]`, `[with_system]` | answers with content and a valid finish reason, for two prompt shapes |
| `chat.usage_adds_up` | reports token usage that adds up |
| `chat.latency` | answers within its own declared budget (exclusive: never runs alongside another case) |
| `stream.well_formed` | streams valid chunks, one finish reason, and ends with `data: [DONE]` |
| `errors.unknown_model` | refuses an unknown model with its declared status, in a JSON error envelope |
| `auth.rejects_bad_key` | refuses a wrong key: on the gateway; **skipped** on the engine, which does not declare `auth` |

The script runs twice:

1. **Last night**: the stack is healthy. Every case passes (the engine's auth case is skipped,
   and says why), and the run is published to `demo/local/out/archive/` as the nightly baseline.
2. **Today**: the stack restarts with a gateway bug: it drops the `data: [DONE]` that ends every
   stream, which leaves clients waiting. `stream.well_formed` fails on the gateway and passes on
   the engine behind it, so the bug is in the gateway, and the comparison with last night lists
   it as the one regression.

```
  case                             gateway   engine
  ...
  stream.well_formed               FAIL      pass

  compared with last night:
  regression: gateway stream.well_formed: pass -> fail
```

Open the `report.html` files the script points to: every case, with what it checked.
`demo/local/out/` also has both run directories and the published archive.

The fake stack can break in other ways too; each is a test of `modelcheck`
(`examples/modelcheck/tests/`) and can be started by hand:

```sh
.venv/bin/python examples/modelcheck/tests/fake_stack.py --defect gateway_error_status
GATEWAY_URL=http://127.0.0.1:8400 ENGINE_URL=http://127.0.0.1:8401 GATEWAY_TOKEN=demo-gateway-key \
  .venv/bin/modelcheck run --profile examples/modelcheck/profiles/stack.toml
```

## Kubernetes: the unattended setup on kind

```sh
demo/kind/up.sh       # about a minute; needs kind, kubectl and docker or podman
demo/kind/test.sh     # about two minutes; asserts the production behaviours
demo/kind/down.sh     # removes the cluster
```

This demo uses `httpcheck`, the minimal example tool, against a small HTTP service.
`up.sh` creates a local kind cluster, builds the runner image and deploys
[`deploy/base`](../deploy/base), unchanged, with a demo overlay that adds:

- `target`: a small HTTP service to test (go-httpbin);
- `s3`: an S3-compatible store to publish to (moto's server), and a Job that creates the bucket;
- the profile, settings and demo credentials.

It then starts a run the way a rollout would (`deploy/trigger.sh`), waits for it, and reads the
published run back from the bucket:

```
==> start a run, as a rollout would (deploy/trigger.sh)
httpcheck: ok (exit 0) -> /work/2026-10-27T01-33-15Z-3be0
published to s3://results/nightly/demo/2026-10-27T01-33-15Z-3be0/
==> read the published run back from the bucket
s3://results/nightly/demo/2026-10-27T01-33-15Z-3be0/: ok, 5 passed, 0 failed, 0 errors, 1 skipped
```

`test.sh` then checks what matters when nobody is watching:

| Scenario | Expected |
|---|---|
| The CronJob's schedule | it fires on its own, and the second nightly run compares with the first |
| The target answers wrongly | exit `1`, the Job fails **without a retry**, the evidence is still published |
| The target is down | exit `3` (your environment), not `1` (the target); still published |

The cluster has its own kubeconfig, `demo/kind/.kubeconfig`: the scripts never read or change
your own clusters or contexts.

| File | What |
|---|---|
| [`kind/up.sh`](kind/up.sh), [`kind/test.sh`](kind/test.sh), [`kind/down.sh`](kind/down.sh) | create and run; assert; remove |
| [`kind/lib.sh`](kind/lib.sh) | shared helpers; every `kubectl` call is pinned to the demo cluster |
| [`kind/overlay/`](kind/overlay) | the demo overlay on top of `deploy/base` |
| [`kind/cluster.yaml`](kind/cluster.yaml) | the one-node cluster |
