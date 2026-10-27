# Deploying

Run your tool unattended: nightly, after a rollout, or from CI, with every run published and the
right people told. A tool made with `scripts/new-app.sh` already has everything below in its own
repository (`container/`, `deploy/`, `.github/workflows/`); this repository has the same files
for the example tool.

To see it working before touching a real cluster, run the kind demo: `demo/kind/up.sh` deploys
`httpcheck` to a local cluster and `demo/kind/test.sh` checks the behaviours below (scheduling,
no retries, exit codes, publication). See [demo/README.md](../demo/README.md).

## How an unattended run works

Unattended means `mytool job`. It starts `mytool run` as a child process and does the delivery
itself, so a run that crashes or overruns still leaves a published record and a notification:

| Setting | Default | Meaning |
|---|---|---|
| `MYTOOL_RUN_BUDGET` | 3000 s | `job` stops the run after this |
| `job --grace` | 60 s | the run starts no new case after `budget - grace`, so it normally finishes on its own; a run still going at the budget gets SIGTERM, then this long before SIGKILL |
| `activeDeadlineSeconds` (Kubernetes) | 3600 s | must exceed the budget, plus time to compare, publish and notify |
| `terminationGracePeriodSeconds` (Kubernetes) | 120 s | must exceed `--grace`, so a run stopped by Kubernetes still publishes |

Keep the four in that order if you change one.

## The image

`container/Containerfile` builds a small, non-root image whose entry point is your tool and
whose default command is `job`.

1. **Pin the dependencies** (needs [uv](https://docs.astral.sh/uv/)):
   ```sh
   scripts/lock.sh
   ```
   This writes `container/requirements.lock`: every dependency, cli-framework included, at an
   exact version with its hashes. The image installs only from the lock, with `--require-hashes`,
   so a rebuild cannot drift and a tampered package fails the build. Rerun it after changing
   `pyproject.toml`'s dependencies, and review the diff like code.
2. **Build and push**, from the repository root:
   ```sh
   podman build -f container/Containerfile -t <registry>/<org>/mytool:<git sha> .
   podman push <registry>/<org>/mytool:<git sha>
   ```
   `docker` works the same way.

## Kubernetes

`deploy/base` is a CronJob running `mytool job --compare --label trigger=nightly` every night at
02:00 UTC:

- **no retries** (`backoffLimit: 0`): a retried run hides the failure it found;
- no Kubernetes API access (`automountServiceAccountToken: false`), a non-root user and a
  read-only root filesystem; `/work` and `/tmp` are scratch volumes;
- the profile is mounted from the `app-profile` ConfigMap at `/etc/app/`; settings come from the
  `app-settings` ConfigMap; credentials from the `app-secrets` Secret.

To deploy:

1. **Write an overlay** from `deploy/overlays/example`, next to your system's own manifests
   (real overlays name your namespace and registry, so keep them out of the tool's repository).
   It sets the namespace, the image, the schedule, the profile file and the settings:

   ```yaml
   configMapGenerator:
     - name: app-profile
       files:
         - profile.toml                          # mounted at /etc/app/profile.toml
     - name: app-settings
       literals:
         - MYTOOL_PROFILE=/etc/app/profile.toml
         - MYTOOL_WORK_DIR=/work
         - MYTOOL_RESULTS_URI=s3://<bucket>/<prefix>
         - MYTOOL_RUN_BUDGET=3000
         - MYTOOL_NOTIFY=slack:env:SLACK_WEBHOOK
   ```

   The profile's targets are usually reached by Service DNS names, given as settings or secrets
   under the variable names the profile declares.
2. **Create the Secret**, outside Git, from `deploy/secrets.env.example`: notifier URLs, storage
   keys, and the targets' credentials.
   ```sh
   kubectl -n <namespace> create secret generic app-secrets --from-env-file=<path> \
     --dry-run=client -o yaml | kubectl -n <namespace> apply -f -
   ```
3. **Apply** it: `kubectl apply -k <your overlay>`.
4. **Run it once by hand** before trusting the schedule:
   ```sh
   job=$(deploy/trigger.sh -n <namespace> --trigger manual --id first-run)
   kubectl -n <namespace> logs -f "job/$job"
   ```

`deploy/trigger.sh` starts a one-off run from the CronJob's own pod specification, so it is
configured exactly like a scheduled one; use it after a rollout (`--trigger rollout --id
<version>`). Its runs are labelled with the trigger, never `trigger=nightly`, so they never
become the baseline for comparisons. It needs only the `app-trigger` Role in `deploy/base`: read
the CronJob, create Jobs. Bind it to whoever triggers runs.

The Job's own status follows the exit code: `0` succeeds; anything else fails the Job, so failed
Jobs are worth an alert. The exit code says why ([exit codes](usage.md#exit-codes)).

## GitHub Actions

| Workflow | When | What |
|---|---|---|
| `tests.yaml` (your tool) / `offline-tests.yaml` (this repository) | every push and pull request | tests and lint; here also types, the demo, manifests, shellcheck and the image |
| `live-run.yaml` (this repository) | nightly, and on demand | runs `job` against a deployed system, publishes, compares with earlier nightly runs, writes the summary on the run page, explains a red exit code |
| `kind-demo.yaml` (this repository) | pull requests touching deployment, and on demand | the kind demo and its checks |

`live-run.yaml` runs the example tool; to use it for yours, change the install step and the
tool's name, and set its repository variables (`RESULTS_URI`, `GROUP`, `NOTIFY`) and secrets
(storage keys, notifier URLs, your targets' variables).

## Storage and credentials

| Destination | Needs |
|---|---|
| `file:///path` | a writable path; in Kubernetes, a mounted volume |
| `s3://bucket/prefix` | `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, or a role (`AWS_PROFILE`, `AWS_ROLE_ARN`, IRSA's `AWS_WEB_IDENTITY_TOKEN_FILE`); `AWS_DEFAULT_REGION` |
| an S3-compatible store (MinIO, Ceph, ...) | as for S3, plus `AWS_ENDPOINT_URL_S3`; buckets are then addressed path-style (`AWS_S3_ADDRESSING_STYLE` overrides) |

Preflight writes a small marker to the destination before the run, so a wrong bucket or key
stops the run with exit 3 instead of losing it at the end. Missing storage credentials skip
publication with a warning, and `job --fallback-output file:///...` catches the run instead.
Credentials are registered for redaction as they are read, so they never reach a log or a
published file.
