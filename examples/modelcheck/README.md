# modelcheck

`modelcheck` is an example tool built on VA that verifies an OpenAI-compatible model-serving
stack layer by layer: a gateway, the router behind it and the engine behind the router. It is
the tool used by the [local demonstration](../../demo/README.md), and shows layers, declared
per-layer behaviour, capabilities, variants and exclusive cases.

| File | Purpose |
|---|---|
| `profiles/stack.yaml` | the profile: three layers, each with its kind, the layer it is behind and its expected behaviour |
| `src/modelcheck/profile.py` | the profile schema and its validation |
| `src/modelcheck/layers.py` | the kinds of layer (`proxy`, `router`, `engine`) and their adapters |
| `src/modelcheck/registry.py` | the case registry |
| `src/modelcheck/cases/` | checks for the model list, chat completion, token usage, latency, streaming, errors and authentication |
| `src/modelcheck/client.py` | a recording HTTP client with Server-Sent Events support |
| `src/modelcheck/cli.py` | the `run` and `profile` commands; the other commands come from the framework |
| `tests/fake_stack.py` | a simulated gateway, router and engine with selectable defects; also runs as a standalone service |
| `tests/test_modelcheck.py` | each defect detected on the correct layer, failure location, comparison, concurrency and preflight |

```sh
modelcheck profile show profiles/stack.yaml
GATEWAY_URL=... ROUTER_URL=... ENGINE_URL=... GATEWAY_TOKEN=... \
  modelcheck run --profile profiles/stack.yaml --html
```
