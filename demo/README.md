# Demos

Two ways to see cli-framework work, using the example tool `httpcheck`: one on your laptop in
a minute, and one on a throwaway Kubernetes cluster that runs it the way production would.

## Local: check a service, get a report

```sh
demo/local/run.sh
```

`httpcheck` checks a public HTTP echo service (httpbin.org) standing in for a deployed one:

| Case | Checks that the service... |
|---|---|
| `health.status` | answers its health path with 200 |
| `health.latency` | answers it within the profile's limit |
| `api.json_content_type` | answers JSON, and says so in `Content-Type` |
| `api.unknown_path[GET]`, `[POST]` | answers 404 for a path that cannot exist |
| `auth.rejects_bad_key` | refuses a wrong key: **skipped** here, because the demo profile does not declare `auth`. A skip always says why; it never reads as a pass |

It writes a run directory under `demo/local/runs/` (open its `report.html`), publishes a copy
to `demo/local/results/` with the manifest last, and prints a summary through the `log:`
notifier. Point it at your own service with `DEMO_URL=http://localhost:8080 demo/local/run.sh`.

Files: [`local/profile.toml`](local/profile.toml) declares the target;
[`local/run.sh`](local/run.sh) runs it.

## Kubernetes: the unattended setup on kind

```sh
demo/kind/up.sh       # about a minute; needs kind, kubectl and docker or podman
demo/kind/test.sh     # about two minutes; asserts the production behaviours
demo/kind/down.sh     # removes the cluster
```

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
