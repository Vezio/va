# Changelog

Pin a commit (see the [README](README.md#use-it-in-your-project)) and read this before moving to
a newer one. Until 1.0, a minor version may change behaviour; each change that can affect an
existing tool is marked **changed**.

## 0.3.0

- **changed**: `--notify` on the command line now replaces the notifiers in `<TOOL>_NOTIFY`,
  like every other flag and its variable; it used to add to them.
- **changed**: run ids carry milliseconds (`2026-10-27T02-08-44-123Z-ab12`), so runs started in
  the same second still sort, and compare, in start order.
- **changed**: `job` gives its run a budget of `--budget` minus `--grace`, and no longer passes
  `<TOOL>_RUN_BUDGET` to it, so the run stops on its own before `job` has to stop it.
- **changed**: `Context.state` is typed as your tool's state, not optional: annotated cases type-check.
- New example tool, `modelcheck`, for OpenAI-compatible serving stacks, and a local demo that
  needs no service: it starts a fake gateway and engine, injects a bug and finds it.
- New guide: [Profiles](docs/profiles.md). Usage, Extending and Deploying rewritten and
  completed; every flag documented, with help text in `--help`.
- Profiles: `Problems.unknown_keys` reports misspelled keys with the closest match;
  `Problems.int_range` checks bounds.
- `scripts/new-app.sh` makes a tool that can ship: the framework pinned to a commit, a hash-locked
  image definition, Kubernetes manifests, a CI workflow and its own README. `--local` for
  framework development.
- S3-compatible stores are addressed path-style when `AWS_ENDPOINT_URL_S3` is set.
- `scripts/install.sh` repairs a virtualenv created without pip, and replaces one on an old Python.
- The progress line counts what runs: `16 checks (7 cases x 2 targets)`.

## 0.2.0

- Renamed from runkit to cli-framework (`import cli_framework`).
- Module state reduced to `REDACTOR`, `PLUGINS` and `DEFAULTS`; names on enums (`RunFile`,
  `Schema`, `ExitCode`).
- Opt-in: parallel cases (`--workers`, `exclusive` cases), case timeouts, HTML, JUnit and
  Prometheus outputs.
- Notifiers, sinks and profile formats as plugins; Slack, webhook, exec, file and log notifiers.
- The kind demo and its checks; the hash-locked runner image.

## 0.1.0

- First version: exit codes, evidence, redacted logging, run directories published manifest
  last, comparison, `job`, the CronJob base.
