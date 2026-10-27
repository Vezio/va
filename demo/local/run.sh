#!/usr/bin/env bash
# run.sh: the local demo. Checks a public HTTP echo service with the example tool, writes a
# run directory with an HTML report, and publishes a copy to a local folder.
#
#   demo/local/run.sh                    against https://httpbin.org
#   DEMO_URL=http://localhost:8080 demo/local/run.sh    against your own service
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -x .venv/bin/httpcheck ] || scripts/install.sh
export DEMO_URL="${DEMO_URL:-https://httpbin.org}"
results="demo/local/results"
code=0
.venv/bin/httpcheck run --profile demo/local/profile.toml --work-dir demo/local/runs \
  --remote-output "file://$PWD/$results" --html --notify log: || code=$?
run="$(find demo/local/runs -mindepth 1 -maxdepth 1 -type d | sort | tail -1)/"
cat <<MSG

The run:          $run
Its HTML report:  ${run}report.html
Published copy:   $results/demo/$(basename "$run")/
Exit code:        $code (0 passed, 1 cases failed, 3 the target was unreachable)
MSG
exit "$code"
