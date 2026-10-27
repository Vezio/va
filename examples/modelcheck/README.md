# modelcheck

`modelcheck` is an example tool built on VA. It verifies an OpenAI-compatible serving
stack layer by layer, and is the tool used by the [local demonstration](../../demo/README.md). It
illustrates the framework's principal capabilities:

- **Layer-by-layer verification.** The same checks run against the gateway and the engine behind
  it. A check that fails on one layer and passes on the other isolates a defect to that layer.
- **Declarative profiles.** The profile declares the behaviour expected of each layer (response
  statuses, latency budgets, the model served), with shared defaults and per-layer overrides,
  and is validated with every problem reported in a single pass. See
  [Profiles](../../docs/profiles.md).
- **Capabilities.** The authentication check runs on layers that declare `auth`, and is skipped,
  with the reason recorded, on layers that do not.
- **Verified checks.** Each check is proven, in the test suite, to fail against a corresponding
  defect of the simulated stack, on the affected layer only.

## Components

| File | Purpose |
|---|---|
| `profiles/stack.yaml` | the profile: a gateway and an engine |
| `src/modelcheck/profile.py` | the profile schema and its validation |
| `src/modelcheck/cases/` | checks for models, chat completion (with variants and an exclusive latency check), streaming, errors and authentication |
| `src/modelcheck/client.py` | a recording HTTP client with Server-Sent Events support |
| `src/modelcheck/cli.py` | the `run` command (preflight checks, then a `Session`) and the `profile` command; other commands are provided by the framework |
| `tests/fake_stack.py` | a simulated engine and gateway with selectable defects; also runs as a standalone service |
| `tests/test_modelcheck.py` | detection of each defect on the correct layer, comparison, concurrency and preflight checks |

## Usage

```sh
modelcheck profile show profiles/stack.yaml
GATEWAY_URL=... ENGINE_URL=... GATEWAY_TOKEN=... modelcheck run --profile profiles/stack.yaml --html
```
