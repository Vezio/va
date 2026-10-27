# Shared by up.sh, test.sh and down.sh. Every kubectl call goes through k(), which pins this
# demo's own kubeconfig and context: nothing here can reach another cluster.
# shellcheck shell=bash

CLUSTER="${CLUSTER:-va-demo}"
NAMESPACE=va-demo
IMAGE=localhost/httpcheck:demo
KUBECONFIG="$(pwd)/demo/kind/.kubeconfig"
export KUBECONFIG

step() { printf '\n==> %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
ok() { printf 'ok: %s\n' "$*"; }
k() { kubectl --kubeconfig "$KUBECONFIG" --context "kind-$CLUSTER" "$@"; }

# Sets ENGINE (docker or podman) for the scripts that build and load images.
choose_engine() {
  export ENGINE
  if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
    ENGINE=docker
  elif command -v podman >/dev/null && podman info >/dev/null 2>&1; then
    ENGINE=podman
    export KIND_EXPERIMENTAL_PROVIDER=podman
  else
    echo "needs a running docker or podman" >&2
    exit 2
  fi
}

# Run Python in a short-lived pod with the runner image and the demo's S3 settings.
in_cluster_python() {
  k -n "$NAMESPACE" delete pod py --ignore-not-found --wait=true >/dev/null
  k -n "$NAMESPACE" run py --image="$IMAGE" --restart=Never --quiet \
    --env=AWS_ENDPOINT_URL_S3=http://s3:5000 --env=AWS_DEFAULT_REGION=us-east-1 \
    --env=AWS_ACCESS_KEY_ID=demo-access-key --env=AWS_SECRET_ACCESS_KEY=demo-secret-key \
    --command -- python -c "$1" >/dev/null
  k -n "$NAMESPACE" wait --for=jsonpath='{.status.phase}'=Succeeded pod/py --timeout=120s >/dev/null \
    || { k -n "$NAMESPACE" logs pod/py >&2; fail "in-cluster python failed"; }
  k -n "$NAMESPACE" logs pod/py
  k -n "$NAMESPACE" delete pod py --wait=false >/dev/null
}

# The published outcome of a run, as "<exit label> <passed> <failed> <errors> <skipped>
# <baseline runs>", read back from the bucket.
published_outcome() {
  in_cluster_python "
import json
from va.publish import Archive
a = Archive.at('s3://results/nightly')
o = a.outcome('demo', '$1')
c = o.counts
try:
    base = json.loads(a.sink.get('demo/$1/comparison.json'))['baseline_runs']
except Exception:
    base = []
print(o.exit_label, c.passed, c.failed, c.errored, c.skipped, len(base))
"
}

# The run id a job wrote, from its log.
run_id_of() {
  k -n "$NAMESPACE" logs "job/$1" | sed -n 's#.*published to s3://results/nightly/demo/\([^/]*\)/.*#\1#p' | tail -1
}
