# httpcheck

`httpcheck` is the minimal example tool built on VA. It verifies any HTTP service
against a profile: health, content type, handling of unknown paths, and authentication.

It serves two purposes: it is the template that `scripts/new-app.sh` copies to create a new
tool, and the tool deployed by the [Kubernetes demonstration](../../demo/README.md). For a more
complete example, see [`modelcheck`](../modelcheck).

## Components

| File | Purpose |
|---|---|
| `src/httpcheck/cli.py` | the `run` command built on `Session`, the `profile` command, and the framework's built-in commands |
| `src/httpcheck/profile.py` | a typed profile, validated with every problem reported in a single pass |
| `src/httpcheck/client.py` | a recording HTTP client: every request and response is retained as evidence |
| `src/httpcheck/cases/` | checks for health, content type, unknown paths and authentication |
| `profiles/example.yaml` | an example profile with a gateway and a backend |
| `tests/fake_server.py` | a local HTTP server with selectable defects |
| `tests/test_httpcheck.py` | each check proven to fail against its defect; exit codes; publication, comparison and `job` |
