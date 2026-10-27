# httpcheck

A complete tool built on cli-framework: it checks HTTP services against a profile. Read it as
the reference for how a tool is put together, or copy it with `scripts/new-app.sh` to start
your own.

Try it with `demo/local/run.sh` from the repository root (see [demo/](../../demo/README.md)).

| File | What it shows |
|---|---|
| `src/httpcheck/cli.py` | the app: a `run` command built on `Session`, a `profile` command, the built-ins |
| `src/httpcheck/profile.py` | a typed profile, validated with every problem reported at once |
| `src/httpcheck/client.py` | a recording client: every request and response kept as evidence |
| `src/httpcheck/cases/` | the cases: health, content type, unknown paths, authentication |
| `profiles/example.toml` | a profile with a gateway and a backend |
| `tests/fake_server.py` | a real local server with switchable defects |
| `tests/test_httpcheck.py` | every case proven to fail against its defect; exit codes; publish, compare, `job` |
