#!/usr/bin/env bash
# run.sh: the local demo. Nothing to set up: no service, no network, no account. It starts a
# small OpenAI-compatible serving stack on this machine (an engine, and a gateway in front of
# it) and checks it with modelcheck, the example tool, the way you would check a real one.
#
#   demo/local/run.sh
#
#   1. Last night: the stack is healthy. The run passes and is published as the nightly
#      baseline.
#   2. Today: the gateway has picked up a bug: its streams no longer end with `data: [DONE]`.
#      The run fails, on the gateway's column only, and the comparison with last night names
#      the regression.
#
# Everything lands in demo/local/out/. Needs Python 3.11 or newer (PYTHON=... to choose one).
# Exits 0 when the demo behaved as described.
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -x .venv/bin/modelcheck ] || scripts/install.sh

out=demo/local/out
rm -rf "$out"
mkdir -p "$out"
archive="file://$PWD/$out/archive"
profile=examples/modelcheck/profiles/stack.toml
stack_pid=""

stop_stack() {
  if [ -n "$stack_pid" ]; then
    kill "$stack_pid" 2>/dev/null || true
    wait "$stack_pid" 2>/dev/null || true
    stack_pid=""
  fi
}
trap stop_stack EXIT

# Start the fake stack on free ports, with the given defects; export its addresses.
start_stack() {
  stop_stack
  local log="$out/stack.log" args=()
  for defect in "$@"; do args+=(--defect "$defect"); done
  : >"$log"
  .venv/bin/python examples/modelcheck/tests/fake_stack.py --engine-port 0 --gateway-port 0 \
    ${args[@]+"${args[@]}"} >"$log" 2>&1 &
  stack_pid=$!
  for _ in $(seq 1 50); do grep -q '^engine ' "$log" && break; sleep 0.1; done
  ENGINE_URL="$(sed -n 's/^engine \([^,]*\),.*/\1/p' "$log")"
  GATEWAY_URL="$(sed -n 's/.*gateway \([^,]*\),.*/\1/p' "$log")"
  [ -n "$ENGINE_URL" ] && [ -n "$GATEWAY_URL" ] || { cat "$log" >&2; exit 1; }
  export ENGINE_URL GATEWAY_URL GATEWAY_TOKEN=demo-gateway-key
  echo "stack: $(cat "$log")"
}

# The verdicts of a run as a table: one row per case, one column per layer.
columns() {
  .venv/bin/python - "$1" <<'EOF'
import json, sys
from pathlib import Path
rows = {}
for line in (Path(sys.argv[1]) / "results.jsonl").read_text().splitlines():
    r = json.loads(line)
    case = r["case_id"] + (f"[{r['variant']}]" if r["variant"] else "")
    rows.setdefault(case, {})[r["target"]] = r["verdict"]
print(f"  {'case':<32} {'gateway':<9} engine")
for case, by in sorted(rows.items()):
    mark = lambda v: v.upper() if v in ("fail", "error") else v  # noqa: E731
    print(f"  {case:<32} {mark(by.get('gateway', '-')):<9} {mark(by.get('engine', '-'))}")
EOF
}

latest_run() { find "$out/runs" -mindepth 1 -maxdepth 1 -type d | sort | tail -1; }

printf '\n=== 1. Last night: a healthy stack\n\n'
start_stack
night=0
.venv/bin/modelcheck run --profile "$profile" --work-dir "$out/runs" --remote-output "$archive" \
  --label trigger=nightly --html || night=$?
first="$(latest_run)"

printf '\n=== 2. Today: the gateway now drops the end of its streams\n\n'
start_stack gateway_drops_done
today=0
.venv/bin/modelcheck run --profile "$profile" --work-dir "$out/runs" --remote-output "$archive" \
  --compare --html || today=$?
second="$(latest_run)"
stop_stack

printf '\n=== What the second run found\n\n'
columns "$second"
printf '\n'
echo "  compared with last night:"
awk '/^### /{show = ($0 ~ /^### Regressions/)} show && /^- /{print "  regression: " substr($0, 3)}' \
  "$second/comparison.md"
printf '\n'
cat <<MSG
The engine passes every case the gateway fails, so the bug is in the gateway: one case,
written once, run against each layer.

  exit codes:        last night $night (passed), today $today (cases failed)
  HTML reports:      $first/report.html
                     $second/report.html
  published runs:    $out/archive/demo-stack/
MSG

[ "$night" = 0 ] && [ "$today" = 1 ] && grep -q "gateway stream.well_formed" "$second/comparison.md"
