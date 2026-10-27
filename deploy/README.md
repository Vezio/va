# deploy

Kubernetes manifests for running a tool unattended. See [docs/deploying.md](../docs/deploying.md).

| Path | What |
|---|---|
| `base/` | the CronJob (runs `<tool> job` nightly) and the Role for triggering runs |
| `overlays/example/` | an overlay with placeholders: copy it next to your system's manifests |
| `trigger.sh` | start a one-off run from the CronJob's pod spec, e.g. after a rollout |
| `secrets.env.example` | the credentials a run can use; fill in outside Git |
