# va

Reference for the `va` package: what each module provides, how the pieces fit
together during a run, and which names form the public API. For task-oriented guides, see the
[documentation](../../docs/README.md); for the rationale behind the design, see
[Design](../../docs/design.md).

## Public API

The names exported from the package root (`from va import ...`) are the supported
API. Other names are reachable from their modules and may change between minor versions before
1.0.

| Area | Names |
|---|---|
| Building a tool | `App`, `Command`, `UsageError`, `run_app`, `add_run_arguments`, `RunOptions` |
| Running checks | `Session`, `Baseline`, `CaseRegistry`, `Case`, `Context`, `Selector`, `Runner`, `Target` |
| Profiles | `Problems`, `ProfileError` (plus `profile.load`, `profile.digest`, `profile.resolve_env`) |
| Layers | `LayerKinds`, `LayerKind`, `LayerAdapter`, `HeaderServedBy`, `AnswersItself`, `LayerLocator`, `Location` |
| Results and runs | `RunDir`, `ResultRecord`, `RunOutcome`, `Verdict`, `Check`, `CaseKey`, `Counts`, `Failure`, `Problem`, `Severity`, `Publication`, `PublicationStatus`, `SkipClass`, `SkipClasses`, `RunFile`, `Schema` |
| Comparison and reporting | `compare`, `Comparison`, `Change`, `ChangeKind`, `RunReport` |
| Publication | `deliver`, `Archive`, `Sink` |
| Lifecycle hooks | `RunHooks`, `HookChain`, `ProgressLog` |
| Notification | `Notifier` (built-in notifiers in `va.notifiers`) |
| Configuration and state | `Settings`, `Key`, `Defaults`, `DEFAULTS`, `RetryPolicy`, `Redactor`, `REDACTOR`, `PluginRegistry`, `Plugins`, `PLUGINS` |
| Exit codes | `ExitCode` |

## How a run fits together

```
App.main()                                    cli.py: arguments, logging, exceptions -> exit codes
  └─ your RunCommand.run()                    your tool: load the profile, build the Session
       ├─ RunOptions.from_args()              commands.py: standard flags -> typed options, RunDir
       └─ Session.execute()                   session.py
            ├─ preflight.run(checks)          preflight.py: exit 3 before anything is tested
            ├─ Runner.run_all(cases, targets) runner.py: each case x target x variant
            │    └─ case(ctx)                 your checks; evidence.py records each observation
            │         -> ResultRecord         model.py, written to results.jsonl by rundir.py
            ├─ RunOutcome, summary.md         model.py, report.py
            ├─ compare_with_archive()         session.py, compare.py, publish.Archive
            ├─ hooks.on_finish()              hooks.py, outputs/ (HTML, JUnit, metrics)
            ├─ publish.deliver()              publish.py, sinks.py: manifest last
            └─ notify_all()                   notifiers/: RunReport to every channel
       -> ExitCode                            exit_codes.py: combine(verdict, delivered)
```

`job` (in `commands.py`) runs the same `run` command as a child process, then performs the
comparison, publication and notification itself.

## Modules

### Entry points and commands

| Module | Provides |
|---|---|
| `cli.py` | `App`: argument parsing, shared logging flags, mapping of exceptions to exit codes, SIGTERM handled as an interruption. `Command`: the protocol a command implements (`name`, `help`, `passthrough`, `add_arguments(parser, app)`, `run(args, app)`, optional `description`). `UsageError`, `run_app`. |
| `commands.py` | The built-in commands (`publish`, `compare`, `notify`, `job`). `add_run_arguments` and `RunOptions`: the standard flags of a tool's `run` command, parsed and validated into typed options. |
| `settings.py` | `Settings` and `Key`: every environment setting, named `<TOOL>_<KEY>`. |

### Running checks

| Module | Provides |
|---|---|
| `session.py` | `Session`: one run end to end, from preflight to the final exit code. `Baseline` and `compare_with_archive`. |
| `runner.py` | `CaseRegistry` and `Case` (checks and their options: `needs`, `variants`, `tags`, `exclusive`, `timeout_s`), `Selector`, `Context` (what a check receives), `Runner` (serial by default; opt-in workers and timeouts), the `Target` protocol (`name`, `capabilities`). |
| `evidence.py` | `Journal`: `check`, `require` and `fail`, which record an observation before deciding. `CheckFailed`. |
| `preflight.py` | Preflight checks (`env_set`, `reachable`, `destination_writable`, `notifiers_valid`), `run` and `blocking`. |
| `hooks.py` | `RunHooks` (`on_start`, `on_result`, `on_finish`, `on_published`), `HookChain` (isolates hook failures), `ProgressLog`. |
| `outputs/` | Optional hooks: `HtmlReport`, `JUnitReport`, `MetricsFile`. |

### Profiles

| Module | Provides |
|---|---|
| `profile.py` | `load` (format chosen by suffix: YAML by default, TOML, JSON, or a plugin), `Problems` (`require`, `optional`, `unknown_keys`, `one_of`, `int_range`, `env_name`, `check_type`, `add`, `raise_if_any`), `ProfileError`, `digest`, `resolve_env`. See [Profiles](../../docs/profiles.md) and [Extending profiles](../../docs/extending-profiles.md). |
| `layers.py` | Targets as the layers of one stack. `LayerKinds`: a tool's registry of kinds (`register(name, depth, adapter=)`), with `parse` and `check_stack` for profiles, `order` (outermost first), `chain`, `locate` (where a case's failure starts). `LayerAdapter` (`request_headers`, `served_by`), `HeaderServedBy`, `AnswersItself`. `LayerLocator`: a run hook writing `layers.json`. The `Layered` protocol (`name`, `kind`, `behind`). See [Profiles: Layers](../../docs/profiles.md#layers). |

### Results, reports and comparison

| Module | Provides |
|---|---|
| `model.py` | The typed records every run file serialises: `ResultRecord`, `RunOutcome`, `Verdict`, `Check`, `CaseKey`, `Counts`, `Failure`, `Problem`, `Severity`, `Publication`, `PublicationStatus`, `SkipClass`, `SkipClasses`. Each reads back with `from_dict`. |
| `rundir.py` | `RunDir`: the run directory, with writers that redact, typed readers (`results`, `outcome`) and the manifest. `new_run_id`. |
| `layout.py` | `RunFile` (every file name in a run directory), `Schema` (every schema identifier), `ArchiveKey`. |
| `report.py` | `RunReport`: what is said about a finished run, rendered by every channel; the Markdown summary. |
| `compare.py` | `compare`, `Comparison`, `Change`, `ChangeKind`: regressions, recoveries, unstable checks, lost and new evidence. |
| `exit_codes.py` | `ExitCode` (each code with its label, headline and meaning), `combine`, `status_of`. |

### Publication and notification

| Module | Provides |
|---|---|
| `sinks.py` | The `Sink` protocol, `FileSink`, `S3Sink`, `sink_for`. |
| `publish.py` | `deliver` (publication with the manifest last; never raises) and `Archive` (published runs read back, baselines). |
| `notifiers/` | The `Notifier` protocol, spec parsing (`parse_spec`, `parse_specs`, `env:NAME` targets), `notify_all`, `post_json`, and the built-in notifiers: `slack`, `webhook`, `exec`, `file`, `log`. |
| `retry.py` | `call`: bounded retries for deliveries. Never used for requests under test. |

### Infrastructure

| Module | Provides |
|---|---|
| `log.py` | `configure` (stderr diagnostics, stdout primary output, redaction on every handler), `LogSetup`, `get_logger`, `emit`. |
| `redact.py` | `Redactor` and the process-wide `REDACTOR`: registered secrets and token patterns, scrubbed from every string written. |
| `plugins.py` | `PluginRegistry` (named factories, discovered through entry points) and `PLUGINS` (`notifiers`, `sinks`, `profile_loaders`). |
| `defaults.py` | `Defaults` and `DEFAULTS` (timeouts, retry policy, truncation limits), `RetryPolicy`. |
| `kube.py` | `KubeContext`: a read-only `kubectl`/`oc` adapter; write verbs are refused by construction. |

## Extension points

| To add | Implement | Register or pass |
|---|---|---|
| A check | a function taking a `Context` | `@REGISTRY.case(...)` in your tool |
| A profile schema | dataclasses and a `load` function using `Problems` | your tool's `run` command |
| A protocol client | any class; record exchanges with `RunDir.append_jsonl` | your tool's state object |
| A layer kind | `LayerKinds.register(...)`, and a `LayerAdapter` where calling it differs | your tool's `LayerKinds`; `kind:` in profiles |
| A preflight check | a callable returning `Problem`s | `Session(checks=...)` |
| Run behaviour | a `RunHooks` subclass | `Session(hooks=...)` |
| A notification channel | a class with `name` and `send(report)` | `PLUGINS.notifiers`, or the `va.notifiers` entry point |
| A storage backend | a class following `Sink` | `PLUGINS.sinks`, or the `va.sinks` entry point |
| A profile format | a function from text to a mapping | `PLUGINS.profile_loaders`, or the `va.profile_loaders` entry point |
| A command | a class following `Command` | `App.add(...)` |
| A skip reason | `SkipClass` | `App(skip_classes=SkipClasses.default().extend(...))` |

[Extending](../../docs/extending.md) gives a worked example of each.

## State and dependencies

Module-level state consists of three objects: `REDACTOR` and `PLUGINS`, which are process-wide
by nature and replaceable where an API accepts an alternative, and `DEFAULTS`, which is
immutable. Configuration is held by objects the application owns: `App`, `Settings` and
`LogSetup`.

The package depends on the Python standard library and PyYAML. `boto3` (the `s3` extra) is
imported only when publishing to S3.
