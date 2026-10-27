# Changelog

Pin a commit (see the [README](README.md#installation)) and read this before moving to
a newer one. Until 1.0, a minor version may change behaviour; each change that can affect an
existing tool is marked **changed**.

## 0.5.0

- New: **layers**, for targets that are the ingresses of one stack (`va.layers`). A tool
  registers its kinds of layer with a depth (`LayerKinds`); each target declares its `kind` and
  the target it is `behind`. Profiles are checked as a stack (unknown target, cycle, a layer in
  front of an outer kind), targets run and report outermost first, and each kind has an adapter
  for the headers it needs and for saying which component answered (`served_by`).
- New: `LayerLocator`, a run hook that places each failure at the layer it starts at (fails
  there, passes behind it) and writes `layers.json`.
- **changed**: modelcheck checks three layers, gateway, router and engine, and every target in its
  profiles needs a `kind`; layers in front of another name it in `behind`. Its fake stack has a
  router, and a router defect (`router_drops_usage`); the local demo now finds that one, and
  places it at the router.
- `scripts/new-app.sh --local` installs the framework checkout in editable mode. It used to
  depend on a `file://` URL, and pip reused a cached wheel of an older framework in its place.

## 0.4.0

- **changed**: renamed to **VA** (Verify and Attribute). The package installs as
  `va-framework` and imports as `va` (formerly `cli-framework` and `cli_framework`). Tools must
  update their dependency and imports. Plugin entry-point groups, schema identifiers, logger
  names and default metric names change accordingly (`va.notifiers`, `va.results/1`, `va_*`).
- **changed**: YAML is the default profile format, in the examples, the scaffold and the
  documentation; PyYAML is now a core dependency. TOML and JSON profiles still load. The `yaml`
  extra remains, empty, so existing pins keep resolving.
- New guides: [Extending profiles](docs/extending-profiles.md), and a
  [package reference](src/va/README.md) for `va`.
- Piping a tool's output into a reader that stops early (`| head`) no longer prints a
  traceback per line.

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
