# cli-framework (runkit)

A skeleton for command-line tools that **test something already deployed**, keep the evidence,
and run both by hand and unattended. It is built for model infrastructure testing and lifecycle
management (inference engines, routers, gateways; new models, upgrades, rollouts) and works for
any deployed service. New tools start with the same structure instead of re-learning it:

- **exit codes that separate blame**: the target, the operator's environment, the tool itself,
  delivery and interruption each get their own code;
- **evidence for every verdict**: checks recorded before deciding, every exchange kept;
- **one redacted logging system**: diagnostics on stderr, primary output on stdout, no secret
  in any stream or file;
- **run directories**: always local, published to `file://` or `s3://` with the manifest last,
  and compared with earlier runs;
- **unattended runs**: a `job` command that survives its own run crashing, a Kubernetes CronJob
  base, an on-demand trigger and GitHub Actions workflows;
- **read-only cluster access**, structurally.

Why each of these exists: [DESIGN.md](DESIGN.md). How the code is laid out and how to extend
it: [ARCHITECTURE.md](ARCHITECTURE.md).

## Layout

| Path | What |
|---|---|
| `src/runkit/` | the library: the primitives every app reuses |
| `examples/httpcheck/` | a complete app built on it (checks HTTP services against a profile); copy it to start |
| `scripts/new-app.sh` | creates a new app from the example, renamed |
| `deploy/` | Kubernetes CronJob base, trigger RBAC, an example overlay, `trigger.sh` |
| `.github/workflows/` | offline tests on every PR; a scheduled and on-demand live run |
| `Containerfile` | the unattended runner image |

## Quick start

```sh
./install.sh --dev                                   # .venv with runkit, httpcheck, ruff, pytest
.venv/bin/python -m pytest tests examples/httpcheck/tests -q

# The example app
.venv/bin/httpcheck profile validate examples/httpcheck/profiles/example.toml
.venv/bin/httpcheck profile show examples/httpcheck/profiles/example.toml
GATEWAY_URL=http://localhost:8080 GATEWAY_TOKEN=... BACKEND_URL=http://localhost:8081 \
  .venv/bin/httpcheck run --profile examples/httpcheck/profiles/example.toml \
  --remote-output file:///tmp/results --label trigger=manual --notify log:
```

A run leaves `runs/<run id>/`:

```
results.jsonl     one versioned record per case, with its checks
exchanges.jsonl   every request and response (app-defined; httpcheck records them)
outcome.json      exit code and class, counts, skips by class, failures, preflight, publication
summary.md        a Markdown summary (also appended to --summary, e.g. $GITHUB_STEP_SUMMARY)
env.json          tool version and the choices made at start
run.log           the DEBUG log, redacted
evidence/         per-case bundles (tracebacks, saved artifacts)
comparison.*      when compared with earlier runs
MANIFEST.json     every file hashed; written and published last
```

## Commands every app gets

| Command | What |
|---|---|
| `<app> run` | your command: preflight, cases, run directory, publish (built on `Session`) |
| `<app> job` | unattended: `run` as a child, then compare, publish (even after a crash), notify |
| `<app> publish RUN_DIR` | publish a finished run, manifest last |
| `<app> compare RUN_DIR` | regressions, recoveries, lost and new evidence against earlier runs |
| `<app> notify RUN_DIR` | send the run's report through any notifiers |

Exit codes: `0` ok, `1` failures, `2` usage, `3` preflight, `4` internal, `5` not delivered,
`6` interrupted.

## Opt-in features

Off by default, so a plain run stays one case at a time with only the core files:

| Flag (variable) | What it adds |
|---|---|
| `--workers N` (`<APP>_WORKERS`) | runs cases on N threads. Cases marked `exclusive=True` (they measure a cache, a latency, a rate limit) still run alone, after the others. Shared state must be thread-safe |
| `--case-timeout S` (`<APP>_CASE_TIMEOUT`), or `timeout_s=` on a case | abandons a case that has not finished and records it as an `error`; the run goes on |
| `--html [PATH]` | `report.html`: one self-contained page (no scripts, nothing loaded), escaped and redacted |
| `--junit [PATH]` | `junit.xml`, which most CI systems render as test results |
| `--metrics-file PATH` (`<APP>_METRICS_FILE`) | Prometheus text metrics, written atomically, e.g. into node_exporter's textfile directory |
| `--tag` / `--exclude-tag` | select cases by tag |
| `--compare` | compare with earlier published runs before publishing |

The image is built from `requirements.lock` (hash-checked; regenerate with `scripts/lock.sh`).

## Notifications

Any channel, chosen by configuration: `--notify KIND:TARGET` (repeatable) or
`<APP>_NOTIFY` (comma-separated). `env:NAME` reads the target from a variable and keeps it out
of every log.

| Kind | Target | Sends |
|---|---|---|
| `slack` | an incoming-webhook URL | a formatted message |
| `webhook` | any http(s) URL | the whole run report as JSON (schema `runkit.report/1`) |
| `exec` | a command | runs it with the report JSON on stdin |
| `file` | a path | appends the report as one JSON line |
| `log` | nothing | the title and counts on stdout |

A new channel (Teams, Discord, email, PagerDuty) is one class registered under a kind, in code
or from another package through the `runkit.notifiers` entry point. Results stores (`SINKS`,
by URI scheme) and profile formats (`LOADERS`, by suffix) extend the same way. See
[ARCHITECTURE.md](ARCHITECTURE.md#extension-points).

## Running unattended

- **Kubernetes:** `deploy/base` is a CronJob running `<app> job` with no retries, a deadline
  above the run budget and a SIGTERM grace period. Write an overlay from
  `deploy/overlays/example`, create the `app-secrets` Secret from `deploy/secrets.env.example`,
  `kubectl apply -k <overlay>`, and start an on-demand run with
  `deploy/trigger.sh -n <namespace> --trigger rollout --id <id>`.
- **GitHub Actions:** `.github/workflows/live-run.yaml` runs nightly and on demand, labels the
  run (`trigger=nightly` or `manual`), compares with earlier nightly runs, writes the summary to
  the run page and explains a red exit code.

## Starting a new tool

```sh
scripts/new-app.sh mytool ../mytool
```

Then replace the profile schema (`profile.py`), the cases (`cases/`) and the client with your
own, keep the fake-server tests, and prove each new case fails against its defect.

## Authors

Built by Tyler Vezio Rimaldi ([@Vezio](https://github.com/Vezio)) and Alberto Perdomo
([@albertoperdomo2](https://github.com/albertoperdomo2)).
