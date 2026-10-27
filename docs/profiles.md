# Profiles

A profile is the file that tells your tool what it is testing: which targets exist, how to reach
them, what each one supports, and how a correct one behaves. The cases stay generic; the profile
makes them specific to one system. The same tool, given a staging profile and a production
profile, tests both.

This guide shows how to design one, using the profile of `modelcheck`, the example tool that
checks an OpenAI-compatible serving stack (the one the [local demo](../demo/README.md) runs).

## What a profile says, and what it must not

| A profile declares | A profile never contains |
|---|---|
| the targets, and the **names** of the variables holding their addresses and keys | addresses, keys or tokens: those come from the environment at run time |
| what each target supports (its capabilities) | what today's system happens to do, when that differs from what it should do |
| what a correct target does: statuses, limits, models served | measurements; those belong in the run's results |

A profile states what a correct system does, and the run measures the deployed system against
it. When the system differs, a case fails and the difference is reported for triage: either the
system is fixed, or the declaration is found to be wrong and is changed deliberately. Editing a
profile to match the system's current behaviour would hide the defects the run exists to find.

## A worked example

[`examples/modelcheck/profiles/stack.yaml`](../examples/modelcheck/profiles/stack.yaml)
describes a gateway, the router behind it, and the engine behind that:

```yaml
name: demo-stack
description: a gateway, the router behind it, and its engine

# Defaults for every target; a target may override them.
model: demo-model
max_latency_ms: 800              # an engine answers directly, so the default is tight

targets:
  - name: gateway
    kind: proxy
    behind: router
    base_url_env: GATEWAY_URL
    key_env: GATEWAY_TOKEN
    capabilities: [models, chat, stream, auth]
    max_latency_ms: 1500         # the gateway adds a hop: its own, looser budget
    expect:
      unknown_model_status: 404  # a correct gateway passes the engine's 404 through
      bad_key_status: 401

  - name: router
    kind: router
    behind: engine
    base_url_env: ROUTER_URL
    capabilities: [models, chat, stream]   # no auth behind the gateway
    max_latency_ms: 1200          # one hop more than the engine
    expect:
      unknown_model_status: 404

  - name: engine
    kind: engine
    base_url_env: ENGINE_URL
    capabilities: [models, chat, stream]   # no auth on the engine: the auth case skips here
    expect:
      unknown_model_status: 404
```

What each part does:

| Feature | In the file | Effect |
|---|---|---|
| **Several targets, one set of cases** | one entry per layer under `targets` | every case runs once per target; comparing the columns shows which layer a problem is in |
| **Layers** | `kind` and `behind` on each target | the targets form a stack, validated as one, run and reported outermost first; a failure is placed at the layer it starts at (see [Layers](#layers)) |
| **Variables, not values** | `base_url_env`, `key_env` | the profile can be committed and shared; the run reads `$GATEWAY_URL` and `$GATEWAY_TOKEN`, and the key is redacted everywhere |
| **Defaults with overrides** | `model`, `max_latency_ms` at the top, overridden per target | say a thing once; differ only where a target really differs |
| **Capabilities** | `capabilities: [...]` | a case that `needs` something a target does not declare is skipped there, with the reason `undeclared`, never silently passed |
| **Declared behaviour** | `expect:` under a target | cases read the expected answer from the profile, so one case serves layers that legitimately answer differently |

Run `modelcheck profile show examples/modelcheck/profiles/stack.yaml` to see the stack
(`gateway (proxy) -> router (router) -> engine (engine)`), what would run on each target, and
what would be skipped and why.

## Layers

When the targets are the ingresses of one stack (a gateway, a router behind it, an engine
behind that), declare them as **layers**. Each target then says two more things:

| Key | Says | Checked |
|---|---|---|
| `kind` | what sort of layer it is: one of the kinds the tool registers (modelcheck: `proxy`, `router`, `engine`) | required; a closed vocabulary |
| `behind` | the name of the target it forwards to; omitted on the innermost layer | names another target; no cycle; never in front of a layer of an outer kind |

In return, the run:

- **orders the layers outermost first**, by kind, whatever order the file lists them in: columns,
  reports and listings read from the client's side inwards;
- **places each failure at the layer it starts at.** A case that fails at the router and passes
  at the engine behind it starts at the router; the gateway in front fails too, but only passes
  the defect on. When the layer behind did not run the case (it skipped, or is not declared),
  the failure is placed at "this layer, or behind it". The run logs one line per failure and
  writes them to `layers.json`;
- **calls each kind through its adapter:** the headers that kind needs on every request, and how
  it says which component answered (`served_by`, recorded with every exchange).

Declare only the layers you can reach. One target is a complete profile; a profile listing a
gateway alone tests the gateway, and a failure there is placed "at the gateway, or behind it".
Layers are optional in the framework: a tool whose targets are not a stack (two regions, a
primary and a replica) leaves `kind` and `behind` out of its schema.

## Designing yours

Start from what your cases need to know, and declare nothing they will not read.

1. **List your targets.** One per layer or endpoint you want a column for: a gateway and its
   backends, a primary and a replica, two regions. Give each a short, stable `name`: it is the
   column heading in every report and the key for comparing runs.
2. **Name, do not write, the connection details.** Each target gets a `base_url_env`, and a
   `key_env` if it needs a credential. Put real values in the environment, or in a
   `<name>.local.yaml` copy that Git ignores.
3. **Choose your capabilities.** One word per feature a target may or may not have (`stream`,
   `auth`, `tools`). Cases declare `needs={...}`; a target without the capability skips the case.
   Keep the vocabulary closed: the loader rejects unknown words, so a typo cannot quietly turn a
   case off.
4. **Declare behaviour where targets differ.** If correct targets may answer differently (a
   status, a limit, a model name), make it a declaration the case reads, with a sensible default.
   If every correct target answers the same way, keep it in the case.
5. **Comment the why.** A declaration that looks odd (a looser budget, an unusual status) needs a
   comment saying why it is right. The next reader cannot ask you.

## Writing the loader

The framework reads the file and gives you the tools to validate it; your tool defines the
schema, as dataclasses. The loader's job is to turn the file into those dataclasses, and to
report **every** problem at once, each with its path, so a profile is fixed in one pass. An
invalid profile exits with code 2, before anything is tested.

From [`examples/modelcheck/src/modelcheck/profile.py`](../examples/modelcheck/src/modelcheck/profile.py):

```python
@dataclass(frozen=True)
class Target:                       # the framework needs `name` and `capabilities`
    name: str
    kind: str                       # with `behind`: a layer (va.layers.Layered)
    behind: str | None
    base_url_env: str
    key_env: str | None
    capabilities: frozenset[str]
    model: str
    max_latency_ms: int
    expect: Expect = field(default_factory=Expect)


def load(path: Path) -> Profile:
    data = profile.load(path)       # YAML (or TOML, JSON), by suffix
    p = Problems()                  # collects problems; validation continues past each one
    p.unknown_keys(data, TOP_KEYS)  # a misspelled key is reported, with the closest match
    name = p.require(data, "name", str)
    default_model = p.optional(data, "model", str)
    ...                             # per target: kind, behind = KINDS.parse(p, raw, at)
    KINDS.check_stack(p, targets)   # rules across layers: behind, cycles, kind order
    p.raise_if_any(str(path))       # every problem at once, or nothing
    return Profile(name, tuple(KINDS.order(targets)), profile.digest(data), path)
```

`KINDS` is the tool's `LayerKinds` registry: see [Extending](extending.md#a-layer-kind).

The `Problems` collector:

| Method | Reports |
|---|---|
| `require(data, key, type, at=...)` | a missing key, or a value of the wrong type |
| `optional(data, key, type, default, at=...)` | a value of the wrong type; returns the default when absent |
| `unknown_keys(data, allowed, at=...)` | keys the schema does not define, with a "did you mean" |
| `one_of(value, allowed, path)` | a value outside a closed vocabulary |
| `int_range(value, low, high, path)` | a number outside its range |
| `env_name(value, path)` | a field meant to name a variable that holds a value instead |
| `add(path, message)` | anything else, e.g. a rule across fields |
| `LayerKinds.parse` / `check_stack` | a layer's `kind` and `behind`, and the stack they form |
| `raise_if_any(source)` | raises `ProfileError` (exit 2) listing everything found |

A profile with mistakes gets every one of them back:

```
$ modelcheck profile validate broken.yaml
error: broken.yaml: 10 problem(s):
  modle: is not a known key; did you mean 'model'?
  name: must be str, not int
  targets[0].capabilites: is not a known key; did you mean 'capabilities'?
  targets[0].kind: must be one of proxy, router, engine, not 'gateway'
  targets[0].capabilities: must be one of models, chat, stream, auth, not 'chats'
  targets[0].key_env: is required when the target declares auth
  targets[0].model: is required (here or at the top of the profile)
  targets[0].expect.unknown_model_status: must be between 100 and 599, not 700
  targets[0].base_url_env: must name an environment variable (UPPER_SNAKE), not hold a value
  targets[0].behind: names no target: 'routr'
```

`profile.digest(data)` hashes the declarations. Record it with each run (the examples put it in
the run's `subject`): when two runs disagree, a changed digest tells the reader the profile
changed before they look at the system.

## File format

Profiles are YAML by default; TOML (`.toml`) and JSON (`.json`) are also accepted, and another
format is a plugin (see [Extending](extending.md#a-profile-format)). The format follows the file's
suffix, and the same schema and validation apply to all of them.

YAML is read with `safe_load`, which never constructs Python objects. Its implicit typing still
deserves care: an unquoted value that looks like a number, a boolean or a date becomes one.

| Written | Read as | Write instead |
|---|---|---|
| `model: 1.10` | the number `1.1` | `model: "1.10"` |
| `region: no` | `false` | `region: "no"` |
| `version: 2024-05-01` | a date | `version: "2024-05-01"` |
| a tab in the indentation | an error | spaces only |

The loader catches most of these: a field declared as a string that arrives as a number or a
boolean is reported with its path (`targets[0].model: must be str, not float`). Quote any string
that could be read as something else.

## Reading declarations in cases

The case gets its target, with everything the profile declared for it:

```python
@REGISTRY.case("errors.unknown_model", needs={"chat"})
def unknown_model(ctx: Context) -> None:
    t = ctx.target
    exchange = ctx.state.client(t).post_json(
        "/v1/chat/completions",
        {"model": "no-such-model", "messages": [{"role": "user", "content": "hi"}]},
        case_id=ctx.case_id,
    )
    ctx.require("http_status", exchange.status, t.expect.unknown_model_status)
```

Never branch on a target's name inside a case (`if t.name == "gateway"`). When targets
legitimately differ, the difference is a declaration; then a new target is a profile change, not
a code change.

## Credentials

Read a key through the redactor, so it is scrubbed from every log, file and notification from
the moment it is read:

```python
token = REDACTOR.secret_from_env(target.key_env) if target.key_env else None
```

Preflight then checks every named variable is set before any case runs, and a missing one exits
with code 3 (your environment), not 1 (the target). See `preflight_checks` in the examples'
`cli.py`.

## Profiles per environment

Keep one profile per system under test (`staging.yaml`, `production.yaml`), with the same
schema. The run's archive group defaults to the profile's `name`, so runs are only compared with
earlier runs of the same profile.

## Extending a schema

Each addition follows the same pattern: allow the key in the loader, add a dataclass field with a
default that preserves existing behaviour, validate it with `Problems`, and read it in cases.

**A setting with a profile-wide default and per-target overrides.** Allow the key at both
levels, read the top-level value once and pass it to each target as its default:

```python
TOP_KEYS = (..., "request_timeout_s")
TARGET_KEYS = (..., "request_timeout_s")

default_timeout = p.optional(data, "request_timeout_s", (int, float), 30.0)
...
timeout = p.optional(raw, "request_timeout_s", (int, float), default_timeout, at=at)
```

**A capability.** Add the word to the closed vocabulary (`CAPABILITIES`). Cases that need it
declare `needs={"tools"}`, and targets that do not declare it skip those cases as `undeclared`.

**Declared behaviour.** Validate lists item by item so that a problem names the exact entry,
and store lists as tuples so that cases cannot modify a declaration:

```python
reasons = p.optional(expect_raw, "finish_reasons", list, None, at=f"{at}.expect")
for i, reason in enumerate(reasons or []):
    p.one_of(reason, FINISH_REASONS, f"{at}.expect.finish_reasons[{i}]")
```

**Values that are credentials.** A field that refers to a secret holds the name of an
environment variable, validated with `p.env_name`, which rejects a pasted value. Read the
value through `REDACTOR.secret_from_env` and add the variable to the preflight checks, so a
missing value stops the run with exit code 3:

```yaml
headers:
  X-Tenant: GATEWAY_TENANT      # the variable holding the value
```

**Rules across fields.** Check them after reading the fields involved, and report the problem on
the field to change, with `p.add(path, message)`.

**Changing a schema in use.** Profiles are often maintained by other teams, so treat the schema
like an API:

| Change | Approach |
|---|---|
| Add a field | Make it optional, with a default that keeps current behaviour. |
| Rename a field | Accept the old name for one release with a logged warning, then remove it. |
| Narrow a field | Treat as a breaking change and give profile owners time to adapt. |
| Remove a field | Reject it with a message naming its replacement rather than ignoring it. |

**Testing.** Test the loader with a valid profile (values, defaults and overrides) and an
invalid one (the exact problems reported), and test a case that reads the new declaration
against a defect in the tool's fake service.

## Checklist

- Every declaration is read by a case.
- No URL, key or token appears in the file; connection details are variable names.
- Capabilities and other vocabularies are closed and validated by the loader.
- Unknown keys are reported, so a typo cannot silently drop a declaration.
- Unusual declarations carry a comment explaining why they are correct.
- For a layered stack, every target has a `kind`, and every layer in front of another names it
  in `behind`.
- `profile validate` and `profile show` succeed, and a test checks the problems the loader
  reports for a broken profile.
