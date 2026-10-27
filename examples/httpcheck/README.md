# httpcheck

The minimal example tool: it checks any HTTP service against a profile (health, content type,
unknown paths, authentication). It is the template `scripts/new-app.sh` copies to start a new
tool, and the tool the [Kubernetes demo](../../demo/README.md) deploys. For a fuller example, see
[`modelcheck`](../modelcheck).

| File | What it shows |
|---|---|
| `src/httpcheck/cli.py` | the app: a `run` command built on `Session`, a `profile` command, the built-ins |
| `src/httpcheck/profile.py` | a typed profile, validated with every problem reported at once |
| `src/httpcheck/client.py` | a recording client: every request and response kept as evidence |
| `src/httpcheck/cases/` | the cases: health, content type, unknown paths, authentication |
| `profiles/example.toml` | a profile with a gateway and a backend |
| `tests/fake_server.py` | a real local server with switchable defects |
| `tests/test_httpcheck.py` | every case proven to fail against its defect; exit codes; publish, compare, `job` |
