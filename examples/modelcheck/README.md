# modelcheck

An example tool built on cli-framework: it checks an OpenAI-compatible serving stack, layer by
layer. It is the tool the [local demo](../../demo/README.md) runs, and a worked example of the
framework's main ideas:

- **one case, many layers**: the same cases run against the gateway and the engine behind it,
  so a failure on one column and a pass on the other points at the layer;
- **a profile that declares intent**: what a correct layer does (statuses, latency budgets, the
  model it serves), with defaults and per-layer overrides, validated with every problem reported
  at once ([Profiles](../../docs/profiles.md) walks through it);
- **capabilities**: the authentication case runs where a layer declares `auth`, and is skipped,
  with the reason, where it does not;
- **cases that prove themselves**: each one fails against a defect of the fake stack in
  `tests/`, on the layer that has the defect, and only there.

| File | What |
|---|---|
| `profiles/stack.toml` | the profile: a gateway and an engine |
| `src/modelcheck/profile.py` | the profile's schema and validation |
| `src/modelcheck/cases/` | models, chat (with variants, and an exclusive latency case), streaming, errors, auth |
| `src/modelcheck/client.py` | a recording HTTP client, with Server-Sent Events parsing |
| `src/modelcheck/cli.py` | `run` (preflight, then a `Session`) and `profile`; the rest is built in |
| `tests/fake_stack.py` | an engine and a gateway with switchable defects; also runs as a service |
| `tests/test_modelcheck.py` | every defect caught on the right layer; comparison; workers; preflight |

```sh
modelcheck profile show profiles/stack.toml
GATEWAY_URL=... ENGINE_URL=... GATEWAY_TOKEN=... modelcheck run --profile profiles/stack.toml --html
```
