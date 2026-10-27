#!/usr/bin/env bash
# test.sh: verify the CronJob's operational behaviour on the demo cluster (run up.sh first).
#
#   1. Scheduling: the CronJob starts runs on schedule, and a later run is compared with an
#      earlier one.
#   2. Failing target: the run exits 1, the Job fails without a retry, and the evidence is
#      published.
#   3. Unreachable target: the run exits 3 rather than 1, and is still published.
#
# The cluster is restored to the state up.sh left it in, whether or not the checks pass.
set -euo pipefail
cd "$(dirname "$0")/../.."
# shellcheck source=demo/kind/lib.sh
source demo/kind/lib.sh

restore() {
  k -n "$NAMESPACE" patch cronjob app --type=merge \
    -p '{"spec":{"schedule":"*/10 * * * *","suspend":false}}' >/dev/null || true
  k -n "$NAMESPACE" patch configmap app-settings --type=merge \
    -p '{"data":{"TARGET_URL":"http://target"}}' >/dev/null || true
  k -n "$NAMESPACE" scale deployment/target --replicas=1 >/dev/null || true
}
trap restore EXIT

k -n "$NAMESPACE" get cronjob app >/dev/null || fail "no demo deployment: run demo/kind/up.sh first"

# Start a one-off run with the current settings and print the Job name once it has finished.
run_once() {
  local job
  job="$(deploy/trigger.sh -n "$NAMESPACE" --cronjob app --trigger test --id "$1-$(date +%s)")"
  k -n "$NAMESPACE" wait --for=jsonpath='{.status.conditions[0].status}'=True "job/$job" \
    --timeout=300s >/dev/null || fail "$job did not finish"
  echo "$job"
}

pods_of() { k -n "$NAMESPACE" get pods -l "job-name=$1" -o name | wc -l | tr -d ' '; }
job_failed() { [ "$(k -n "$NAMESPACE" get job "$1" -o jsonpath='{.status.failed}')" = "1" ]; }
exit_code_of() {
  k -n "$NAMESPACE" get pods -l "job-name=$1" \
    -o jsonpath='{.items[0].status.containerStatuses[0].state.terminated.exitCode}'
}

step "1. scheduling: the CronJob fires on its own; nightly runs compare"
k -n "$NAMESPACE" delete jobs --all --wait=true >/dev/null
k -n "$NAMESPACE" patch cronjob app --type=merge -p '{"spec":{"schedule":"* * * * *"}}' >/dev/null
scheduled=()
for _ in $(seq 1 60); do
  scheduled=()
  while read -r name; do scheduled+=("$name"); done < <(k -n "$NAMESPACE" get jobs \
    -o jsonpath='{range .items[?(@.metadata.ownerReferences[0].kind=="CronJob")]}{.metadata.name}{" "}{.status.succeeded}{"\n"}{end}' \
    | awk '$2 == 1 {print $1}' | sort)
  [ "${#scheduled[@]}" -ge 2 ] && break
  sleep 5
done
[ "${#scheduled[@]}" -ge 2 ] || fail "the CronJob did not complete two scheduled runs in 5 minutes"
k -n "$NAMESPACE" patch cronjob app --type=merge -p '{"spec":{"suspend":true}}' >/dev/null
ok "scheduled runs: ${scheduled[*]}"
first="$(run_id_of "${scheduled[0]}")"
second="$(run_id_of "${scheduled[1]}")"
read -r label _ _ _ _ baselines <<<"$(published_outcome "$second")"
[ "$label" = ok ] || fail "scheduled run $second ended $label"
[ "$baselines" = 1 ] || fail "run $second compared with $baselines baseline runs, expected 1 ($first)"
ok "the second nightly run ($second) compared with the first ($first)"

step "2. a failing target: exit 1, no retry, still published"
k -n "$NAMESPACE" patch configmap app-settings --type=merge \
  -p '{"data":{"TARGET_URL":"http://s3:5000"}}' >/dev/null   # answers, but not like the target
job="$(run_once failing)"
job_failed "$job" || fail "$job did not fail"
[ "$(exit_code_of "$job")" = 1 ] || fail "$job exited $(exit_code_of "$job"), expected 1"
[ "$(pods_of "$job")" = 1 ] || fail "$job ran $(pods_of "$job") pods: it was retried"
read -r label _ failed _ _ _ <<<"$(published_outcome "$(run_id_of "$job")")"
[ "$label" = failures ] && [ "$failed" -gt 0 ] || fail "published outcome: $label, $failed failed"
ok "exit 1, one pod, published as failures ($failed failed cases)"
k -n "$NAMESPACE" patch configmap app-settings --type=merge \
  -p '{"data":{"TARGET_URL":"http://target"}}' >/dev/null

step "3. an unreachable target: exit 3 (environment), not 1, still published"
k -n "$NAMESPACE" scale deployment/target --replicas=0 >/dev/null
k -n "$NAMESPACE" wait --for=delete pod -l app=target --timeout=120s >/dev/null || true
job="$(run_once unreachable)"
[ "$(exit_code_of "$job")" = 3 ] || fail "$job exited $(exit_code_of "$job"), expected 3"
[ "$(pods_of "$job")" = 1 ] || fail "$job was retried"
read -r label passed failed _ _ _ <<<"$(published_outcome "$(run_id_of "$job")")"
[ "$label" = environment ] && [ "$passed$failed" = 00 ] || fail "published outcome: $label"
ok "exit 3, nothing tested, published as environment"

step "all CronJob behaviours hold"
