# Usage

Every tool built on the framework has the same commands, flags and outputs. Here the tool is
called `mytool`; the examples are `modelcheck` and `httpcheck`. `mytool <command> --help` prints
all of this for your tool, with its own variable names.

## Commands

| Command | What it does |
|---|---|
| `mytool run --profile P` | checks the targets in a profile: preflight, the cases, the run directory; and, when asked, compare, publish and notify |
| `mytool job --profile P` | the unattended run (CronJob, CI): `run` as a child process, then compare, publish and notify, even if the run crashed |
| `mytool publish RUN_DIR` | publishes a finished run directory |
| `mytool compare RUN_DIR` | compares a run with earlier published runs |
| `mytool notify RUN_DIR` | sends a finished run's report |
| `mytool profile validate P`, `show P` | checks a profile offline; lists what would run or skip on each target |

`run` and `profile` are written by each tool (a few lines each, on the framework's `Session` and
profile helpers); `job`, `publish`, `compare` and `notify` come with the framework.

## Exit codes

The exit code says whose problem a run is, so CI and people can act without reading logs.

| Code | Meaning | Whose problem |
|---|---|---|
| `0` | everything selected passed | nobody's |
| `1` | cases failed | the target's |
| `2` | bad arguments or an invalid profile | the invocation |
| `3` | preflight failed: a variable unset, a target unreachable, a destination unwritable; nothing was tested | the environment |
| `4` | the tool failed internally | the tool's |
| `5` | the run passed, but results or a notification were not delivered | delivery |
| `6` | interrupted (Ctrl-C, SIGTERM) or out of time; finished cases are kept | nobody's yet |

A delivery problem never hides a failure: a run that fails cases and also fails to publish
exits `1`.

## Settings: flags and variables

Most flags have an environment variable, named `<TOOL>_<SETTING>`, where `<TOOL>` is the tool's
name in capitals with anything else turned into `_` (`my-tool` gives `MY_TOOL`). A flag on the
command line wins over its variable. CronJobs and CI set the variables; people usually use flags.

| Variable | Flag |
|---|---|
| `MYTOOL_PROFILE` | `--profile` |
| `MYTOOL_WORK_DIR` | `--work-dir` |
| `MYTOOL_RESULTS_URI` | `--remote-output` |
| `MYTOOL_FALLBACK_URI` | `job --fallback-output` |
| `MYTOOL_GROUP` | `--group` |
| `MYTOOL_NOTIFY` | `--notify` (comma-separated) |
| `MYTOOL_BASELINE_LABEL` | `--baseline-label` |
| `MYTOOL_RUN_BUDGET` | `--budget` |
| `MYTOOL_WORKERS` | `--workers` |
| `MYTOOL_CASE_TIMEOUT` | `--case-timeout` |
| `MYTOOL_METRICS_FILE` | `--metrics-file` |

## `run`

```sh
mytool run --profile profiles/staging.yaml -k chat --html
```

| Flag | |
|---|---|
| `--profile P` | the profile file |
| **Output** | |
| `--work-dir DIR` | where run directories are created (default `runs`) |
| `--run-dir DIR` | create the run directory exactly here (`job` sets this) |
| `--remote-output URI` | also publish the run: `file:///path`, `s3://bucket/prefix`, or a plugin's scheme |
| `--group NAME` | archive partition: runs are only compared within a group (the examples default to the profile's name) |
| `--summary FILE` | also append the Markdown summary to FILE, e.g. `$GITHUB_STEP_SUMMARY` |
| `--label KEY=VALUE` | label the run (repeatable); `trigger=nightly` makes it a comparison baseline |
| `--link NAME=URL` | show a link in the report and notifications, e.g. `ci=https://...` (repeatable) |
| `--notify KIND:TARGET` | send the report (repeatable; see [Notifications](#notifications)) |
| **Selection** | |
| `-k TEXT` | only cases whose id contains TEXT (repeatable: any may match) |
| `--tag TAG` | only cases with this tag (repeatable: all must match) |
| `--exclude-tag TAG` | skip cases with this tag (repeatable) |
| **Comparison** | |
| `--compare` | compare with earlier published runs before publishing; needs `--remote-output` |
| `--compare-window N` | how many earlier runs must all have passed before a failure is a regression (default 1) |
| `--baseline-label KEY=VALUE` | only runs with this label are baselines (default `trigger=nightly`; empty for any run) |
| `--baseline RUN_ID` | compare with this one run instead |
| **Execution** | |
| `--workers N` | opt-in: run cases on N threads; cases marked `exclusive` still run alone (default 1) |
| `--case-timeout SECONDS` | opt-in: abandon a case after SECONDS and record it as an `error`; a case can set its own `timeout_s` |
| `--budget SECONDS` | start no new case after SECONDS; the run ends as interrupted (exit 6) |
| **Reports** (opt-in) | |
| `--html [PATH]` | write `report.html`: one self-contained page, nothing loaded from outside |
| `--junit [PATH]` | write `junit.xml`, which most CI systems show as test results |
| `--metrics-file PATH` | write Prometheus metrics, atomically, e.g. for node_exporter's textfile collector |

Every command also takes `--log-level` (diagnostics on stderr; the run's own `run.log` always
has everything), `--log-format json`, `-q` and `--no-color`. Reports and summaries go to stdout,
diagnostics to stderr, so `mytool run ... > report.txt` keeps the report.

## `job`

```sh
mytool job --profile /etc/mytool/profile.yaml --compare --label trigger=nightly
```

`job` runs `run` as a child process, then compares, publishes and notifies itself, so a run that
crashes or overruns still leaves a published record and a notification. Flags `job` does not
define are passed to `run` unchanged (`--profile`, `-k`, `--label`, `--html`, ...).

| Flag | |
|---|---|
| `--work-dir`, `--remote-output`, `--group`, `--notify` | as for `run`, but done by `job` |
| `--compare`, `--compare-window`, `--baseline-label`, `--baseline` | as for `run` |
| `--fallback-output URI` | publish here instead when `--remote-output` cannot be used here (e.g. no credentials) |
| `--budget SECONDS` | stop the run after SECONDS (default 3000). The run itself starts no new case after `SECONDS - grace`, so it normally finishes on its own; keep it below a Kubernetes Job's `activeDeadlineSeconds` |
| `--grace SECONDS` | how long a stopped run has to finish before it is killed (default 60) |

A run that overruns is stopped and ends as interrupted (exit `6`). A run that crashes, or has
to be killed before it writes its outcome, is recorded as `crashed` (exit `4`). Either way `job`
still publishes what there is, and notifies.

## `publish`, `compare`, `notify`

All three work on a finished run directory, e.g. one copied from a CI artifact:

```sh
mytool publish runs/2026-10-27T02-08-44-123Z-ab12 --remote-output s3://results/staging --group staging
mytool compare runs/2026-10-27T02-08-44-123Z-ab12 --remote-output s3://results/staging --group staging
mytool notify  runs/2026-10-27T02-08-44-123Z-ab12 --notify slack:env:SLACK_WEBHOOK
```

`publish` and `compare` need `--remote-output` (or its variable). The group defaults to the one
recorded in the run's `outcome.json`, which is the group the run was created with; `--group` (or
its variable) overrides it. A destination whose scheme no sink supports is a usage error (exit
`2`). `notify` needs `--notify`. `compare` reports and exits `0`; add `--fail-on-regression` to exit `1` when there is
a regression.

## The run directory

Every run writes `<work dir>/<run id>/` locally, however it ends, and publishes a copy to
`<remote-output>/<group>/<run id>/` when asked. Run ids sort by start time.

```
results.jsonl     one record per case: verdict, the checks it recorded, timing
outcome.json      how the run ended: exit code, counts, skips by class, failures, publication
summary.md        a readable summary
env.json          the tool's version and the choices made at start
run.log           the full diagnostic log, redacted
evidence/         per-case files: tracebacks, anything a case saved
exchanges.jsonl   every request and response (written by the examples' recording client)
comparison.json, comparison.md       with --compare
report.html, junit.xml, metrics.prom with the report flags
MANIFEST.json     every other file, hashed; written and published last
```

A published run is complete exactly when its `MANIFEST.json` is there; readers ignore runs
without one.

## Verdicts

| Verdict | Means |
|---|---|
| `pass` | the target did the right thing; the recorded checks show what was checked |
| `fail` | the target did the wrong thing; the failed check shows the value |
| `error` | the case could not reach a verdict: it crashed, or timed out |
| `skip` | not run, with a class saying why: `undeclared` (the target does not declare what the case needs), `not_implemented` (the tool cannot test it yet), `requires_instrumentation`, `excluded_by_policy`, `throttled`, or one your tool adds |

## Comparing runs

`--compare` (on `run` or `job`), or the `compare` command, compares a run with the latest earlier
published run of the same group that carries the baseline label, `trigger=nightly` unless you
say otherwise; label your scheduled runs with it (`--label trigger=nightly`, which the CronJob in
`deploy/` does). An ad-hoc run is then never anyone's baseline.

| Change | Means |
|---|---|
| regression | passed before, fails or errors now |
| recovery | failed before, passes now |
| flaky | with `--compare-window N`: the earlier runs disagree, so a failure now is not called a regression |
| lost evidence | had a verdict before, skipped now |
| new evidence | skipped before, fails now |
| new case | no earlier result |

## Notifications

`--notify KIND:TARGET`, repeatable, or comma-separated in `MYTOOL_NOTIFY`. A target written
`env:NAME` is read from the variable `NAME` and kept out of every log: use it for anything
secret, such as a webhook URL.

| Kind | Target | Sends |
|---|---|---|
| `slack` | a Slack incoming-webhook URL (`https://`) | a formatted message |
| `webhook` | any `http(s)` URL | the whole report as JSON, for any system that accepts a webhook |
| `exec` | a command | runs it, without a shell, with the report as JSON on stdin; a non-zero exit is a failed delivery |
| `file` | a path | appends the report as one JSON line |
| `log` | nothing (`log:`) | the title and counts on stdout |

```sh
mytool run --profile p.yaml --notify slack:env:SLACK_WEBHOOK --notify webhook:env:ALERTS_URL
```

Each channel is delivered on its own, with bounded retries; one failing does not stop the others,
and a failed delivery turns a passing run into exit `5`. Because the variable is comma-separated,
a target cannot contain a comma: put such a command in a script and use `exec:/path/to/script`.
Another channel is one class: see [Extending](extending.md#a-notification-channel).
