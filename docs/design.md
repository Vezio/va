# Design

The lessons cli-framework encodes, and why each exists. Every rule here guards against a confident
wrong answer, a lost run or a leaked key. Read this before changing a primitive; the code
follows from it.

## What kind of tool this is for

A CLI that points at something **already deployed**, runs checks against it, keeps the
evidence, and runs unattended on a schedule as well as by hand. The home ground is model
infrastructure and its lifecycle: inference engines, routers and gateways in front of them,
and the moments that change them (a new model, an engine upgrade, a config rollout, a
rollback). The same shell fits any deployed service.

It tests; it does not deploy. Different apps vary the *checks* and the *commands*; the shell
around them (exit codes, logs, evidence, publishing, scheduling) stays the same, and that
shell is cli_framework.

Typical apps built on it:

- **API conformance:** does each layer (engine, router, gateway) answer the APIs it claims,
  with the right shapes, errors and streaming behaviour? One case, one result per layer.
- **Release qualification:** run before and after an upgrade or a new model, compare the two
  runs, and gate the rollout on regressions.
- **Nightly health:** the same suite on a schedule, published and compared with the previous
  nights, with a notification that says whose problem a red run is.
- **Agentic or workload suites:** hand tasks to an external harness, poll it, and keep its
  verdicts and evidence in the same run-directory shape.

## 1. Exit codes separate blame

| Code | Class | Whose problem |
|---|---|---|
| 0 | ok | nobody's |
| 1 | failures | the target's: the only code that blames it |
| 2 | usage | the invocation or the profile |
| 3 | environment | the operator's environment: nothing was tested |
| 4 | internal | the tool's own defect, never a verdict |
| 5 | not_delivered | the verdict stands, but results or the notification did not arrive |
| 6 | interrupted | stopped; what finished is kept |

**Why:** a connection refused against `localhost` is almost always a dead tunnel, not a target
bug. Reported as a test failure, it teaches people to distrust the suite. With separate codes,
CI tells "the target is broken" from "the runner is broken" without reading logs.
**Rule:** never widen 1 to cover the environment, and never let 5 mask a verdict
(`exit_codes.combine`). `ExitCode` is an IntEnum and `OK` is 0, so it is falsy: compare with
`is None`, never with `or`.

## 2. Preflight before anything is tested

Credentials set, targets reachable, the results destination writable, all checked before the
first case, and every problem reported at once. A destination found to be broken after an
hour-long run is an hour lost. A problem that limits the evidence rather than the cases is a
warning and the run goes on.

## 3. Declare, then verify

A profile states what a **correct** target does, not what today's target happens to do. The
run is the measurement. A declaration nobody verifies is a comment, and a profile fitted to
today's behaviour reports green by not looking.

- A case `needs` capabilities; a target that does not declare them yields a `skip` of class
  `undeclared`, never a silent pass and never a missing row.
- Committed profiles name environment variables (`base_url_env`, `key_env`), never values.
  Real detail lives in `*.local.*` files that Git ignores.
- An invalid profile is reported with **every** problem and its path, in one pass, and exits 2.
- Record a `digest` of the declarations on every run: one tightened declaration often explains
  a whole delta between runs, so the reader needs to see it first.

## 4. Two axes: target and case

A case receives a target and knows nothing of its internals. One case written once yields one
comparable result per target. Comparing the columns (target A passes, target B fails) is what
locates a defect, and it only means something while cases stay target-agnostic. Add a third
axis (a wire format, a client) the same way: orthogonal, injected, never branched on inside
a case.

## 5. A verdict carries its evidence

Assertions **record what they observed before they decide**, on passes as well as failures
(`ctx.check`, `ctx.require`, `ctx.fail`). A failure shows the value that caused it. A pass
with no recorded checks is indistinguishable from a case that checked nothing, so the runner
warns about it, and a failure whose checks all passed is flagged as contradicting itself.
Every request/response a case makes goes to `exchanges.jsonl`.

## 6. Four outcomes, and skips that say what is missing

`pass`, `fail` (the target), `error` (the case could not reach a verdict), `skip`. Each skip
carries a class: `undeclared` (target gap), `not_implemented` (tool gap),
`requires_instrumentation`, `excluded_by_policy`, `throttled`, plus any an app registers.
Never collapse them: a matrix that renders "the target lacks this" and "we cannot test this
yet" as one grey box reads as coverage when it is not.

## 7. Test requests are never retried; deliveries are, within bounds

A retried request hides the failure the case exists to catch. A retried upload or
notification only preserves evidence, so `retry.call` retries those, with a cap, and
honours Retry-After only up to the time the run has left. Keep `retry` out of case code.

## 8. Local first, publish after, manifest last

The run directory is always written locally. Publishing to `file://` or `s3://` is a separate
step over the finished directory, and it happens however the run ended, so a crash still
leaves evidence. `MANIFEST.json` hashes every file and is uploaded **last**: a run is complete
in storage exactly when its manifest is there, and readers list only those. Verification and
publication are separate sections of `outcome.json`; the published copy cannot know how its
own upload went.

## 9. One logging system, redacted at the handler

No `print` (ruff T20). Diagnostics go to stderr under `--log-level`; primary output goes to
stdout through `emit`, which the level never suppresses, so `app show | jq` works at any log
level. The redaction filter sits **on the handlers**, not on a logger: a logger's filter does
not see records from its child loggers, so a filter on the root logger silently misses every
`app.module` logger. HTTP libraries log headers at DEBUG, and the run's log is published.

Redaction happens in three places: the log handlers, every run-file writer (a server can echo
a credential inside a body), and notifications. Secrets enter through
`redact.secret_from_env`, which registers them as they are read.

## 10. Never mutate what you measure

Cluster access goes through `kube.py`, whose verb allowlist (`get`, `logs`, `version`,
`config`, `auth can-i`) leaves no code path to `apply`, `delete`, `scale` or `exec`. The
guarantee is only worth having if it is structural. The tool never starts or supervises a
port-forward: it prints the command and preflight checks it. An app that must write gets a
separate, explicit adapter; it does not widen this one.

## 11. The unattended run outlives the run

`job` runs `<app> run` as a **child process**, so if the run is OOM-killed or overruns its
budget, the parent still records a `crashed` outcome, compares, publishes and notifies. The
CronJob has `backoffLimit: 0` (a retried run hides a failure), an `activeDeadlineSeconds`
above the budget plus publish time, a grace period for SIGTERM, and no service account token
unless the run reads the cluster. One pod spec serves both the schedule and on-demand runs
(`deploy/trigger.sh` clones it), so a triggered run is configured exactly like a nightly one.

## 12. Comparing runs: four transitions

Keyed by (target, case, variant); only pass, fail and error are verdicts.

| Transition | Reading |
|---|---|
| pass to fail/error | a regression |
| fail to pass | a recovery, reported on purpose: silence after a fix erodes trust |
| verdict to skip | lost evidence, not a failure |
| skip to fail | new evidence, not new breakage |

Zero cases is never a pass. With a window of N baselines, a case the window disagrees about
is `flaky`, not a page. Only runs labelled as baselines (`trigger=nightly`) are compared
against, so an ad-hoc run never becomes the reference.

## 13. argparse, thin commands

argparse, because Click and Typer write help and errors straight to the terminal, which
fights the single-logger contract, and because `parse_known_args` plainly forwards unknown
arguments (how `job` passes selection through to `run`). `argparse.REMAINDER` swallows later
flags of our own; it is avoided. Commands stay thin: parse, build a config, call a library
function, so the library works without the CLI.

## 14. Prove a case can fail

Tests run against a **real local server with injectable defects**, not mocks. Every case is
proven to fail against its defect, with the deciding check recorded as not-ok. A case that has
never failed has not been tested. The tool's own tests need no network beyond localhost and no
cluster, so they run anywhere, in parallel with nothing.

## 15. Raw protocol first; SDKs are witnesses

Cases speak the raw protocol through a recording client. An SDK normalises away much of what a
test exists to see (malformed bodies, unexpected fields, statuses, headers). Use an SDK only
where SDK compatibility is itself the requirement, and pin it exactly: a floating version makes
results incomparable across runs.

## 16. Extension points are registries; the channel is configuration

Anything an organisation will want to swap (where notifications go, where results are stored,
what format profiles are in) is a named plugin in a `PluginRegistry`, chosen by a string at run
time (`--notify slack:env:SLACK_WEBHOOK`, `--remote-output gs://...`, `profile.jsonnet`) and
discoverable through Python entry points. Slack is one notifier among several, not a special
case: the generic `webhook` posts the whole `RunReport` as JSON, `exec` pipes it to any command,
and a new channel is one class. A tool built on cli-framework never hard-codes a vendor.

## 17. One report, many renderings

What is said about a run is computed once (`RunReport`) and rendered by each channel: the
summary file, the terminal, Slack, a webhook payload. Channels cannot disagree about what
happened, and a new channel only decides how to format, never what to say.

## 18. Typed records, versioned schemas

Every run file is a serialised dataclass that reads back into the same type, with enums
instead of magic strings. Readers ignore fields they do not know, so a newer writer does not
break an older reader; renaming or removing a field needs a new schema version, because
published runs outlive the code that wrote them.

## 19. No hidden state

Module-level state is three named objects: `REDACTOR` and `PLUGINS`, process-wide by nature
and injectable, and `DEFAULTS`, immutable. Everything else that used to float at module level
lives on a type: file names and schema ids on enums, an exit code's label and meaning on the
code itself, the skip vocabulary behind `SkipClasses.default()`. Configuration lives in objects
the app holds (`App`, `Settings`, `LogSetup`), and every setting has one name in one place
(`settings.Key`), shared by its flag, its environment variable, the CronJob and CI. A tunable
that would otherwise be a literal in a function body is a field of `Defaults`, with a comment
saying what it bounds.

## 20. The unattended parent owns delivery

`job` publishes and notifies; the `run` it starts does not, even though it inherits the same
environment: the job strips its own delivery settings from the child's environment. Otherwise
every run would publish and notify twice, and a crashed child would leave a half-delivered
run that the parent then delivers again.

## 21. Power features are opt-in, and the default stays the safe one

Concurrency, timeouts and extra outputs are off unless asked for. The default run is serial
because against shared, rate-limited infrastructure, concurrency changes what is measured:
latencies, cache hits and throttling all depend on what else is in flight. With `--workers`,
cases that measure such things are marked `exclusive` and still run alone. Timeouts record an
`error`, never a `fail`, because the tool gave up; a case that wants a latency verdict asserts
it. A flag that changes what a result means is never on by default.

## 22. Reproducible images

The runner image installs third-party packages from a hash-checked lock, then the tool itself
with `--no-deps` and a `pip check`. A rebuild cannot drift, and a tampered package fails the
build. The lock is generated for the image's platform, not the developer's.

