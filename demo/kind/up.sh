#!/usr/bin/env bash
# up.sh: the Kubernetes demo, end to end, on a local kind cluster.
#
#   demo/kind/up.sh            create the cluster (if needed), deploy, run once, show results
#   demo/kind/down.sh          delete the cluster
#
# It builds the runner image, loads it into kind, applies demo/kind/overlay (deploy/base plus
# a target service and an S3-compatible store), starts a run with deploy/trigger.sh, waits for
# it, and reads the published run back from the bucket. Works with docker or podman.
#
# Safety: every kubectl call uses demo/kind/.kubeconfig, which holds only this cluster's
# context. Your own kubeconfig and clusters are never read or changed.
set -euo pipefail
cd "$(dirname "$0")/../.."

# shellcheck source=demo/kind/lib.sh
source demo/kind/lib.sh

for tool in kind kubectl; do
  command -v "$tool" >/dev/null || { echo "up.sh: needs $tool" >&2; exit 2; }
done
choose_engine

step "cluster $CLUSTER ($ENGINE)"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind export kubeconfig --name "$CLUSTER" --kubeconfig "$KUBECONFIG" >/dev/null
  echo "reusing it"
else
  kind create cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" \
    --config demo/kind/cluster.yaml --wait 120s
fi

step "build and load the runner image"
"$ENGINE" build -q -f container/Containerfile -t "$IMAGE" . >/dev/null
if [ "$ENGINE" = podman ]; then
  archive="$(mktemp -t va-demo.XXXXXX).tar"
  trap 'rm -f "$archive"' EXIT
  podman save -q -o "$archive" "$IMAGE"
  kind load image-archive "$archive" --name "$CLUSTER"
else
  kind load docker-image "$IMAGE" --name "$CLUSTER"
fi

step "deploy: the CronJob, a target, an S3 store"
k apply -k demo/kind/overlay
k -n "$NAMESPACE" rollout status deployment/target --timeout=180s
k -n "$NAMESPACE" rollout status deployment/s3 --timeout=180s
k -n "$NAMESPACE" wait --for=condition=complete job/create-bucket --timeout=180s
k -n "$NAMESPACE" get cronjob app

step "start a run, as a rollout would (deploy/trigger.sh)"
job="$(KUBECONFIG="$KUBECONFIG" deploy/trigger.sh -n "$NAMESPACE" --cronjob app --trigger demo)"
echo "job: $job"
if ! k -n "$NAMESPACE" wait --for=condition=complete "job/$job" --timeout=300s; then
  k -n "$NAMESPACE" logs "job/$job" --tail=50 || true
  echo "up.sh: the run did not complete" >&2
  exit 1
fi
k -n "$NAMESPACE" logs "job/$job"

step "read the published run back from the bucket"
run_id="$(run_id_of "$job")"
read -r label passed failed errors skipped _ <<<"$(published_outcome "$run_id")"
echo "s3://results/nightly/demo/$run_id/: $label, $passed passed, $failed failed, $errors errors, $skipped skipped"

step "done"
cat <<MSG
The CronJob also runs on its own every ten minutes. Explore with:
  export KUBECONFIG=$KUBECONFIG
  kubectl -n $NAMESPACE get cronjob,jobs,pods
Remove everything with: demo/kind/down.sh
MSG
