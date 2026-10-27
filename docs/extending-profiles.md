# Extending profiles

A tool's profile grows with its checks: each new check usually needs something new declared.
This guide shows how to extend a profile schema safely, using `modelcheck`'s profile
([`profile.py`](../examples/modelcheck/src/modelcheck/profile.py)) as the running example. Read
[Profiles](profiles.md) first for what a profile is and how it is validated.

## How a profile is processed

```
stack.yaml ──profile.load()──▶ dict ──your load()──▶ Profile, Target, Expect ──▶ checks, preflight, client
                                         │
                                         └── Problems: every problem, with its path (exit 2)
```

The framework reads the file and provides the validation helpers (`Problems`); your tool owns
the schema. Extending a profile therefore always touches the same places in your `profile.py`:

1. the **allowed keys** (`TOP_KEYS`, `TARGET_KEYS`, ...), so `unknown_keys` accepts the new one;
2. the **dataclass** that carries it, with a default;
3. the **loader**, which reads and validates it;

and then whatever **uses** it: a check, the client, or a preflight check. The sections below are
recipes for the common kinds of extension. Each snippet is part of one complete, tested loader.

## A setting with a default and per-target overrides

Example: a request timeout, defaulting to 30 seconds, set once for the profile and overridden
where a target needs more.

```yaml
request_timeout_s: 20            # every target...
targets:
  - name: gateway
    request_timeout_s: 45        # ...unless it says otherwise
```

Allow the key at both levels, and add the field with its default:

```python
TOP_KEYS = ("name", "description", "model", "max_latency_ms", "request_timeout_s", "targets")
TARGET_KEYS = (..., "request_timeout_s", ...)

@dataclass(frozen=True)
class Target:
    ...
    request_timeout_s: float = 30.0
```

Read the profile-wide value once, and pass it to each target as its default:

```python
default_timeout = p.optional(data, "request_timeout_s", (int, float), 30.0)
...
request_timeout_s = p.optional(raw, "request_timeout_s", (int, float), timeout, at=at) or 30.0
```

`p.optional` returns the default when the key is absent and reports a value of the wrong type
with its path. Accepting `(int, float)` lets profiles write `30` as well as `30.5`; a boolean is
still rejected.

Then use it where it applies; here, the client:

```python
self._clients[target.name] = RecordingClient(
    self.run,
    target.base_url,
    token=token,
    timeout=target.request_timeout_s,
    layer=target,
    adapter=KINDS.adapter(target),
)
```

## A capability

Example: tool calling, which some layers support and some do not.

```python
CAPABILITIES = ("models", "chat", "stream", "auth", "tools")
```

```yaml
capabilities: [models, chat, stream, auth, tools]
```

Checks that exercise it declare `needs={"tools"}`. A target without the capability skips them
with the reason `undeclared`, and `profile show` lists the skip. Because the vocabulary is closed
(`p.one_of`), a misspelled capability is an error, not a check silently turned off.

## Declared behaviour, including lists

Example: the finish reasons a layer may legitimately return. A list is validated item by item,
so a problem names the exact entry.

```yaml
expect:
  finish_reasons: [stop, length, tool_calls]
```

```python
EXPECT_KEYS = ("unknown_model_status", "bad_key_status", "finish_reasons")
FINISH_REASONS = ("stop", "length", "tool_calls", "content_filter")

@dataclass(frozen=True)
class Expect:
    ...
    finish_reasons: tuple[str, ...] = ("stop", "length")


reasons = p.optional(expect_raw, "finish_reasons", list, None, at=f"{at}.expect")
for i, reason in enumerate(reasons or []):
    p.one_of(reason, FINISH_REASONS, f"{at}.expect.finish_reasons[{i}]")
...
finish_reasons=tuple(reasons) if reasons else Expect.finish_reasons,
```

The check reads the declaration instead of hard-coding it:

```python
finish = choice.get("finish_reason")
ctx.require("finish_reason", finish, t.expect.finish_reasons, finish in t.expect.finish_reasons)
```

Store lists as tuples and mappings as new dictionaries: the dataclasses are frozen, and a check
must never be able to change a declaration for the checks after it.

## Nested mappings that carry credentials

Example: extra headers a gateway requires, such as a tenant identifier. The profile maps each
header to the **name** of the variable that holds its value, exactly as `key_env` does:

```yaml
headers:
  X-Tenant: GATEWAY_TENANT
```

```python
def _headers(p: Problems, raw: Any, at: str) -> dict[str, str]:
    """headers: {Header-Name: VARIABLE_NAME}. The value names a variable, never holds one."""
    headers = p.optional(raw, "headers", dict, {}, at=at) or {}
    out = {}
    for header, variable in headers.items():
        if p.env_name(variable, f"{at}.headers.{header}"):
            out[str(header)] = variable
    return out
```

`p.env_name` rejects a value that is not a variable name, so a secret pasted into the profile
is refused before it is ever committed or published:

```
targets[0].headers.X-Tenant: must name an environment variable (UPPER_SNAKE), not hold a value
```

Read the values through the redactor, so they are scrubbed from every log, file and report, and
add them to preflight, so a missing one stops the run with exit 3 before any check runs:

```python
headers = {name: REDACTOR.secret_from_env(var) for name, var in target.headers.items()}
...
checks += [preflight.env_set(var) for t in profile.targets for var in t.headers.values()]
```

Any new field that refers to a credential follows this pattern, and ends in `_env` or holds
variable names.

## Rules across fields

Some problems involve more than one field. Check them in the loader, after reading the fields
involved, and report them on the field to change:

```python
if request_timeout_s * 1000 <= max_latency_ms:
    p.add(f"{at}.request_timeout_s",
          f"must be longer than max_latency_ms ({max_latency_ms} ms), or every slow answer "
          "is cut off before it can be judged")
```

Existing examples: `auth` requires `key_env`; target names must be unique.

## Evolving the schema without breaking profiles

Profiles live in other repositories, CronJob ConfigMaps and CI settings, often maintained by
other teams. Change the schema the way you would change an API:

| Change | How |
|---|---|
| **Add a field** | Optional, with a default that keeps today's behaviour. Existing profiles keep working unchanged. |
| **Rename a field** | Accept the old name for one release, with a warning, then remove it. |
| **Narrow a field** (fewer values allowed) | Treat as breaking: announce it in the changelog, and give profile owners a release to adapt. |
| **Remove a field** | Reject it with a message that says what replaced it, rather than silently ignoring it. |

A rename, accepted under its old name for one release:

```python
RENAMED = {"timeout_s": "request_timeout_s"}  # old key -> new key, accepted for one release


def renamed(data: Any, at: str = "") -> None:
    """Accept keys renamed in this release under their old name, with a warning."""
    if not isinstance(data, dict):
        return
    for old, new in RENAMED.items():
        if old in data and new not in data:
            log.warning("%s%s is renamed %s; the old name stops working in the next release",
                        f"{at}." if at else "", old, new)
            data[new] = data.pop(old)
```

Call it before `unknown_keys`, at each level the field can appear: `renamed(data)` for the top
level, `renamed(raw, at)` for each target.

**The digest.** `profile.digest(data)` hashes the declarations as written, so it changes when a
profile changes, not when your defaults do. A changed digest between two runs tells the reader
the profile changed before they look at the system. A default that changes behaviour does not
change any digest: record it in your changelog.

## Testing the extension

Every extension gets two kinds of test, as in `examples/modelcheck/tests/`:

1. **The loader.** A valid profile using the new field loads with the expected values, defaults
   and overrides; an invalid one reports exactly the expected problems:

   ```python
   def test_headers_must_name_variables(tmp_path):
       path = tmp_path / "p.yaml"
       path.write_text(
           "name: x\nmodel: m\ntargets:\n  - name: g\n    base_url_env: G_URL\n"
           "    headers:\n      X-Tenant: secret-value\n"
       )
       with pytest.raises(ProfileError) as exc:
           load(path)
       assert exc.value.problems == [
           "targets[0].headers.X-Tenant: must name an environment variable (UPPER_SNAKE), "
           "not hold a value"
       ]
   ```

2. **The behaviour.** A check that reads the new declaration fails against a defect of the fake
   service when the declaration says it should: for `finish_reasons`, a service that returns an
   undeclared finish reason.

Run `profile validate` on every real profile after a schema change, and `profile show` to see
what now runs where.

## Checklist

- The key is allowed at every level it may appear, and nowhere else.
- The dataclass field has a default that preserves existing behaviour.
- The loader validates type, range or vocabulary, and reports problems with full paths.
- Credentials are variable names, read through the redactor and checked in preflight.
- Checks read the declaration; none branches on a target's name.
- Renames are accepted for one release with a warning; removals are rejected with guidance.
- Tests cover the loader (valid and invalid) and the behaviour (against a defect).
- The changelog records any change a profile owner must act on.
