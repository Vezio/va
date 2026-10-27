#!/bin/sh
# trigger.sh: start a one-off run from the CronJob's own pod spec, e.g. after a rollout.
#
#   trigger.sh -n NAMESPACE [--cronjob app] [--cli kubectl|oc] [--trigger rollout] [--id ID]
#              [-- extra arguments passed through to `<app> run`...]
#
# The Job is the CronJob's template with its arguments replaced by
# `job --label trigger=<trigger> --label <trigger>=<id> <extra...>`, so a triggered run is
# configured exactly like a scheduled one. Prints the Job name for use with other tooling.
# Requires permission to get CronJobs and create Jobs (the app-trigger Role).
set -eu

namespace="" cronjob="app" cli="kubectl" trigger="rollout" id=""
while [ $# -gt 0 ]; do
    case "$1" in
        -n | --namespace) namespace="$2"; shift 2 ;;
        --cronjob) cronjob="$2"; shift 2 ;;
        --cli) cli="$2"; shift 2 ;;
        --trigger) trigger="$2"; shift 2 ;;
        --id) id="$2"; shift 2 ;;
        --) shift; break ;;
        -h | --help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "trigger.sh: unknown option '$1' (try --help)" >&2; exit 2 ;;
    esac
done
[ -n "$namespace" ] || { echo "trigger.sh: -n NAMESPACE is required" >&2; exit 2; }
case "$cli" in kubectl | oc) ;; *) echo "trigger.sh: --cli must be kubectl or oc" >&2; exit 2 ;; esac
command -v jq >/dev/null || { echo "trigger.sh: needs jq" >&2; exit 2; }

id="${id:-$(date -u +%Y%m%d%H%M%S)}"
# Job names are limited to 63 characters; longer names are truncated and suffixed with a
# checksum.
suffix=$(printf '%s' "$id" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | sed 's/^-*//; s/-*$//')
job="${cronjob}-${trigger}-${suffix:-run}"
if [ "${#job}" -gt 63 ]; then
    sum=$(printf '%s' "$job" | cksum | cut -d' ' -f1)
    job="$(printf '%s' "$job" | cut -c1-52 | sed 's/-*$//')-${sum}"
fi

extra=$(jq -cn '$ARGS.positional' --args -- "$@")
"$cli" -n "$namespace" get cronjob "$cronjob" -o json |
    jq --arg job "$job" --arg trigger "$trigger" --arg id "$id" --argjson extra "$extra" '
      {apiVersion: "batch/v1", kind: "Job",
       metadata: {name: $job, labels: ((.metadata.labels // {}) + {"app.kubernetes.io/trigger": $trigger})},
       spec: (.spec.jobTemplate.spec
              | .template.spec.containers[0].args =
                  (["job", "--compare", "--label", ("trigger=" + $trigger),
                    "--label", ($trigger + "=" + $id)] + $extra))}' |
    "$cli" -n "$namespace" create -f - -o name >/dev/null
printf '%s\n' "$job"
