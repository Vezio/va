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

The second column matters. A profile states what a **correct** system does, and the run
measures the real one against it. When the system differs, a case fails, and that failure is
the finding: someone fixes the system, or decides the declaration was wrong and changes it, on
the record. A profile edited to match whatever the system does today reports green by not
looking.

## A worked example

[`examples/modelcheck/profiles/stack.toml`](../examples/modelcheck/profiles/stack.toml)
describes a gateway, and the engine behind it:

```toml
name = "demo-stack"
description = "the gateway and the engine behind it"

# Defaults for every target; a target may override them.
model = "demo-model"
max_latency_ms = 800

[[targets]]
name = "gateway"
base_url_env = "GATEWAY_URL"
key_env = "GATEWAY_TOKEN"
capabilities = ["models", "chat", "stream", "auth"]
max_latency_ms = 1500           # the gateway adds a hop: its own, looser budget

[targets.expect]
unknown_model_status = 404      # a correct gateway passes the engine's 404 through
bad_key_status = 401

[[targets]]
name = "engine"
base_url_env = "ENGINE_URL"
capabilities = ["models", "chat", "stream"]   # no auth on the engine: the auth case skips here

[targets.expect]
unknown_model_status = 404
```

What each part does:

| Feature | In the file | Effect |
|---|---|---|
| **Several targets, one set of cases** | two `[[targets]]` | every case runs once per target; comparing the two columns shows which layer a problem is in |
| **Variables, not values** | `base_url_env`, `key_env` | the profile can be committed and shared; the run reads `$GATEWAY_URL` and `$GATEWAY_TOKEN`, and the key is redacted everywhere |
| **Defaults with overrides** | `model`, `max_latency_ms` at the top, overridden per target | say a thing once; differ only where a target really differs |
| **Capabilities** | `capabilities = [...]` | a case that `needs` something a target does not declare is skipped there, with the reason `undeclared`, never silently passed |
| **Declared behaviour** | `[targets.expect]` | cases read the expected answer from the profile, so one case serves layers that legitimately answer differently |

Run `modelcheck profile show examples/modelcheck/profiles/stack.toml` to see what would run on
each target, and what would be skipped and why.

## Designing yours

Start from what your cases need to know, and declare nothing they will not read.

1. **List your targets.** One per layer or endpoint you want a column for: a gateway and its
   backends, a primary and a replica, two regions. Give each a short, stable `name`: it is the
   column heading in every report and the key for comparing runs.
2. **Name, do not write, the connection details.** Each target gets a `base_url_env`, and a
   `key_env` if it needs a credential. Put real values in the environment, or in a
   `<name>.local.toml` copy that Git ignores.
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
    base_url_env: str
    key_env: str | None
    capabilities: frozenset[str]
    model: str
    max_latency_ms: int
    expect: Expect = field(default_factory=Expect)


def load(path: Path) -> Profile:
    data = profile.load(path)       # TOML, JSON or YAML, by suffix
    p = Problems()                  # collects problems; validation continues past each one
    p.unknown_keys(data, TOP_KEYS)  # a misspelled key is reported, with the closest match
    name = p.require(data, "name", str)
    default_model = p.optional(data, "model", str)
    ...
    p.raise_if_any(str(path))       # every problem at once, or nothing
    return Profile(name, tuple(targets), profile.digest(data), path)
```

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
| `raise_if_any(source)` | raises `ProfileError` (exit 2) listing everything found |

A profile with mistakes gets every one of them back:

```
$ modelcheck profile validate broken.toml
error: broken.toml: 8 problem(s):
  modle: is not a known key; did you mean 'model'?
  name: must be str, not int
  targets[0].capabilites: is not a known key; did you mean 'capabilities'?
  targets[0].capabilities: must be one of models, chat, stream, auth, not 'chats'
  targets[0].key_env: is required when the target declares auth
  targets[0].model: is required (here or at the top of the profile)
  targets[0].expect.unknown_model_status: must be between 100 and 599, not 700
  targets[0].base_url_env: must name an environment variable (UPPER_SNAKE), not hold a value
```

`profile.digest(data)` hashes the declarations. Record it with each run (the examples put it in
the run's `subject`): when two runs disagree, a changed digest tells the reader the profile
changed before they look at the system.

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

Keep one profile per system you test (`staging.toml`, `production.toml`), with the same schema.
The run's archive group defaults to the profile's `name`, so runs are only ever compared with
earlier runs of the same profile.

## Checklist

- Every declaration is something a case reads.
- No URL, key or token in the file; every connection detail is a variable's name.
- Capabilities and other vocabularies are closed, and checked by the loader.
- Unknown keys are reported, so a typo cannot silently drop a declaration.
- Odd-looking declarations carry a comment saying why they are right.
- `profile validate` and `profile show` pass, and a test feeds the loader a broken profile and
  checks the problems it reports.
