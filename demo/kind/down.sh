#!/usr/bin/env bash
# down.sh: delete the demo's kind cluster and its kubeconfig. No other resources are changed.
set -euo pipefail
cd "$(dirname "$0")/../.."
# shellcheck source=demo/kind/lib.sh
source demo/kind/lib.sh
choose_engine
kind delete cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG"
rm -f "$KUBECONFIG"
