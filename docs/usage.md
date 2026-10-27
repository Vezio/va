# Usage

Every tool built on the framework has the same commands, flags and outputs. The examples use
`mytool`; the example app is `httpcheck`.

## Commands

| Command | What it does |
|---|---|
| `mytool run --profile P` | preflight, run the cases, write the run directory; optionally compare, publish and notify |
| `mytool job --profile P` | the unattended run: `run` as a child process, then compare, publish and notify, even if the run crashed |
| `mytool publish RUN_DIR` | publish a finished run directory |
| `mytool compare RUN_DIR` | compare a run with earlier published runs |
| `mytool notify RUN_DIR` | send a finished run's report |
| `mytool profile validate P` / `show P` | check a profile offline; list what would run or skip |

## Exit codes

The exit code says whose problem a run is, so CI and people can act without reading logs.

| Code | Meaning | Whose problem |
|---|---|---|
| `0` | everything selected passed | nobody's |
| `1` | cases failed | the target's |
| `2` | bad arguments or an invalid profile | the invocation |
| `3` | preflight failed; nothing was tested | the environment (credentials, tunnel, destination) |
| `4` | the tool failed internally | the tool's |
| `5` | passed, but results or a notification were not delivered | delivery |
| `6` | interrupted, or the time budget ran out; finished cases are kept | nobody's yet |

A delivery problem never hides a failure: a failing run that also fails to publish exits `1`.

## Flags of `run` (and `job`)

| Flag | Variable | |
|---|---|---|
| `--profile P` | `MYTOOL_PROFILE` | the profile file |
| `-k TEXT` | | cases whose id contains TEXT (repeatable) |
| `--tag T`, `--exclude-tag T` | | select by tag |
| `--label KEY=VALUE` | | label the run, e.g. `trigger=nightly` |
| `--link NAME=URL` | | a link shown in the report, e.g. the CI run |
| `--work-dir DIR` | `MYTOOL_WORK_DIR` | where run directories go (default `runs/`) |
| `--remote-output URI` | `MYTOOL_RESULTS_URI` | publish to `file:///path`, `s3://bucket/prefix` or a plugin scheme |
| `--group NAME` | `MYTOOL_GROUP` | archive partition (default: the profile name) |
| `--compare` | | compare with earlier published runs before publishing |
| `--notify KIND:TARGET` | `MYTOOL_NOTIFY` | where to send the report (repeatable) |
| `--summary FILE` | | append the Markdown summary, e.g. `$GITHUB_STEP_SUMMARY` |
| `--budget SECONDS` | `MYTOOL_RUN_BUDGET` | no new case starts after this |

Every variable is the flag's default, so a CronJob, a CI workflow and a person at a terminal
configure a run the same way. The prefix is the tool's name in capitals.

## Opt-in features

Off unless you ask: a plain run is one case at a time and writes only the core files.

| Flag | Variable | |
|---|---|---|
| `--workers N` | `MYTOOL_WORKERS` | run cases on N threads. Cases marked `exclusive=True` (they measure a cache, latency or a rate limit) still run alone |
| `--case-timeout S` | `MYTOOL_CASE_TIMEOUT` | give up on a case after S seconds and record it as an `error`; a case can set its own `timeout_s` |
| `--html [PATH]` | | `report.html`: a self-contained page, nothing loaded from outside |
| `--junit [PATH]` | | `junit.xml`, shown as test results by most CI systems |
| `--metrics-file PATH` | `MYTOOL_METRICS_FILE` | Prometheus metrics, e.g. for node_exporter's textfile collector |

## The run directory

Every run writes `runs/<run id>/` locally, whatever happens, and publishes a copy if asked:

```
results.jsonl     one record per case: verdict, the checks it recorded, timing
outcome.json      how the run ended: exit code, counts, skips by class, failures, publication
summary.md        a readable summary
exchanges.jsonl   every request and response (written by the example's client)
evidence/         per-case files: tracebacks, saved artifacts
run.log           the full debug log, redacted
env.json          tool version and the choices made at start
comparison.*      with --compare
report.html, junit.xml, metrics.prom    with the opt-in flags
MANIFEST.json     every file hashed; written and uploaded last
```

A published run is complete exactly when its `MANIFEST.json` is there.

## Results: four outcomes

| Verdict | Means |
|---|---|
| `pass` | the target did the right thing, and the checks show what was checked |
| `fail` | the target did the wrong thing; the failed check shows the value |
| `error` | the case could not reach a verdict (it crashed or timed out) |
| `skip` | not run, with a class saying why: `undeclared` (the target lacks it), `not_implemented` (the tool cannot test it yet), `requires_instrumentation`, `excluded_by_policy`, `throttled` |

## Comparing runs

`--compare` (or `mytool compare RUN_DIR`) compares with the latest published run labelled
`trigger=nightly`, so an ad-hoc run never becomes the reference. It reports:

| | |
|---|---|
| regression | passed before, fails now |
| recovery | failed before, passes now |
| flaky | the earlier runs disagree (with `--compare-window N`) |
| lost evidence | had a verdict, now skipped |
| new evidence | was skipped, now fails |

## Notifications

`--notify KIND:TARGET`, repeatable, or comma-separated in `MYTOOL_NOTIFY`. `env:NAME` reads the
target from a variable and keeps it out of every log.

| Kind | Target | Sends |
|---|---|---|
| `slack` | an incoming-webhook URL | a formatted message |
| `webhook` | any http(s) URL | the whole report as JSON |
| `exec` | a command | runs it with the report JSON on stdin |
| `file` | a path | appends the report as one JSON line |
| `log` | nothing | the title and counts on stdout |

```sh
mytool run --profile p.toml --notify slack:env:SLACK_WEBHOOK --notify webhook:env:ALERTS_URL
```

Each channel is delivered independently. Need another one? See [Extending](extending.md).
