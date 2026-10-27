# deploy

Kubernetes manifests for unattended operation of a tool. For the complete procedure, see
[Deploying](../docs/deploying.md).

| Path | Purpose |
|---|---|
| `base/` | the CronJob, which runs `<tool> job` nightly, and the Role used to trigger runs on demand |
| `overlays/example/` | an example overlay with placeholders, to be copied alongside the target system's manifests |
| `trigger.sh` | starts a one-off run from the CronJob's pod specification, for example after a rollout |
| `secrets.env.example` | the credentials a run may use; to be completed outside version control |
