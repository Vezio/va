# Deploying

Run your tool on a schedule and on demand, with results published and someone told. The pieces
are in `deploy/` (Kubernetes), `.github/workflows/` (CI) and `container/` (the image).

To see all of this working before touching a real cluster, run the kind demo:
`demo/kind/up.sh` deploys it to a local cluster and `demo/kind/test.sh` asserts the behaviours
below (scheduling, no retries, exit codes, publication). See [demo/README.md](../demo/README.md).

## How an unattended run works

Everything runs `mytool job`. It starts `mytool run` as a child process, so if the run crashes
or runs out of time, the job still records what happened, compares, publishes and notifies.
The job does the delivery; the child only runs.

## Kubernetes

`deploy/base` is a CronJob that runs `job` nightly:

- **no retries** (`backoffLimit: 0`): a retried run hides the failure it found;
- a **deadline** above the run budget, and a **grace period** so a stopped run still publishes;
- a non-root, read-only container with no Kubernetes API access.

To deploy:

1. **Build and push the image** from the repository root:
   ```sh
   podman build -f container/Containerfile -t <registry>/<org>/mytool:<sha> .
   podman push <registry>/<org>/mytool:<sha>
   ```
2. **Write an overlay** from `deploy/overlays/example`: namespace, image, profile, schedule and
   settings (`MYTOOL_RESULTS_URI`, `MYTOOL_NOTIFY`, ...). Keep real overlays with your system's
   own manifests, not in this repository.
3. **Create the Secret** from `deploy/secrets.env.example` (notifier URLs, storage keys, target
   credentials), outside Git.
4. **Apply** it: `kubectl apply -k <overlay>`.
5. **Run it once by hand** before trusting the schedule:
   ```sh
   job=$(deploy/trigger.sh -n <namespace> --trigger manual --id first-run)
   kubectl -n <namespace> logs -f "job/$job"
   ```

`deploy/trigger.sh` starts a one-off run from the CronJob's own pod spec, e.g. after a rollout,
labelled with the trigger so it never becomes a nightly baseline. It needs only the
`app-trigger` Role (read the CronJob, create Jobs).

## GitHub Actions

| Workflow | When | What |
|---|---|---|
| `offline-tests.yaml` | every PR and push | tests, lint, types, manifest rendering, shellcheck, image build |
| `live-run.yaml` | nightly, and on demand | runs `job` against your target, publishes, compares with earlier nightly runs, puts the summary on the run page, explains a red exit code |

Configure `live-run.yaml` with repository variables (`RESULTS_URI`, `GROUP`, `NOTIFY`) and
secrets (storage keys, notifier URLs, the target's variables).

## The image

`container/Containerfile` installs third-party packages from `container/requirements.lock`
with every file hash-checked, then the tool itself, then runs `pip check`. A rebuild cannot
drift, and a tampered package fails the build. After changing a dependency, regenerate the lock
with `scripts/lock.sh` and review the diff like code.

## Storage and credentials

| Destination | Needs |
|---|---|
| `file:///path` | a writable path (in Kubernetes, a volume) |
| `s3://bucket/prefix` | the `s3` extra; `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (or a role); `AWS_ENDPOINT_URL_S3` for an S3-compatible store |

Missing storage credentials skip publication with a warning instead of failing the run.
Credentials are registered for redaction as they are read, so they never reach a log or a
published file.
