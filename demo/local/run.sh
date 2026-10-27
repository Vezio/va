#!/usr/bin/env bash
# run.sh: run the local demonstration.
#
#   demo/local/run.sh
#
# Starts a simulated OpenAI-compatible serving stack on this machine (a gateway, a router and an
# engine) and verifies it with modelcheck. No external service, network access or credentials
# are required. The script performs two runs:
#
#   1. Baseline: the stack is healthy. The run passes and is archived as the nightly baseline.
#   2. Regression: the router strips token usage from the completions it forwards. The affected
#      case fails at the router and at the gateway, passes at the engine, and the run reports the
#      router as the layer where the failure starts. The comparison with the baseline lists the
#      regressions.
#
# Output is written to demo/local/out/. Requires Python 3.11 or later (set PYTHON to choose an
# interpreter). Exits 0 when both runs produce the expected results.
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -x .venv/bin/modelcheck ] || scripts/install.sh

out=demo/local/out
rm -rf "$out"
mkdir -p "$out"
archive="file://$PWD/$out/archive"
profile=examples/modelcheck/profiles/stack.yaml
stack_pid=""

stop_stack() {
  if [ -n "$stack_pid" ]; then
    kill "$stack_pid" 2>/dev/null || true
    wait "$stack_pid" 2>/dev/null || true
    stack_pid=""
  fi
}
trap stop_stack EXIT

# Start the simulated stack on free ports with the given defects, and export its addresses.
start_stack() {
  stop_stack
  local log="$out/stack.log" args=()
  for defect in "$@"; do args+=(--defect "$defect"); done
  : >"$log"
  .venv/bin/python examples/modelcheck/tests/fake_stack.py \
    --gateway-port 0 --router-port 0 --engine-port 0 ${args[@]+"${args[@]}"} >"$log" 2>&1 &
  stack_pid=$!
  for _ in $(seq 1 50); do grep -q '^gateway ' "$log" && break; sleep 0.1; done
  GATEWAY_URL="$(sed -n 's/^gateway \([^,]*\),.*/\1/p' "$log")"
  ROUTER_URL="$(sed -n 's/.* router \([^,]*\),.*/\1/p' "$log")"
  ENGINE_URL="$(sed -n 's/.* engine \([^,]*\),.*/\1/p' "$log")"
  [ -n "$GATEWAY_URL" ] && [ -n "$ROUTER_URL" ] && [ -n "$ENGINE_URL" ] || {
    cat "$log" >&2
    exit 1
  }
  export GATEWAY_URL ROUTER_URL ENGINE_URL GATEWAY_TOKEN=demo-gateway-key
  echo "stack: $(cat "$log")"
}

# Print a run's verdicts as a table with one row per case and one column per layer (outermost
# first, in the order of layers.json), followed by the layer where each failure starts.
columns() {
  .venv/bin/python - "$1" <<'EOF'
import json, sys
from pathlib import Path
run = Path(sys.argv[1])
layers = json.loads((run / "layers.json").read_text())
names = [layer["name"] for layer in layers["stack"]]
starts = {}
for f in layers["failures"]:
    case = f["case_id"] + (f"[{f['variant']}]" if f["variant"] else "")
    starts[case] = f["layer"] + ("" if f["confirmed"] else " or behind")
rows = {}
for line in (run / "results.jsonl").read_text().splitlines():
    r = json.loads(line)
    case = r["case_id"] + (f"[{r['variant']}]" if r["variant"] else "")
    rows.setdefault(case, {})[r["target"]] = r["verdict"]
mark = lambda v: v.upper() if v in ("fail", "error") else v  # noqa: E731
print(f"  {'case':<30}" + "".join(f"{n:<9}" for n in names) + "starts at")
for case, by in sorted(rows.items()):
    cells = "".join(f"{mark(by.get(n, '-')):<9}" for n in names)
    print(f"  {case:<30}{cells}{starts.get(case, '')}".rstrip())
EOF
}

latest_run() { find "$out/runs" -mindepth 1 -maxdepth 1 -type d | sort | tail -1; }

printf '\n=== 1. Last night: a healthy stack\n\n'
start_stack
night=0
.venv/bin/modelcheck run --profile "$profile" --work-dir "$out/runs" --remote-output "$archive" \
  --label trigger=nightly --html || night=$?
first="$(latest_run)"

printf '\n=== 2. Today: the router now strips token usage from what it forwards\n\n'
start_stack router_drops_usage
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
chat.usage_adds_up fails at the gateway and at the router, and passes at the engine behind
them: the defect starts at the router, and the gateway only passes it on. One case, written
once, run against each layer; the profile's kind and behind say which layer is behind which.

  exit codes:        last night $night (passed), today $today (cases failed)
  HTML reports:      $first/report.html
                     $second/report.html
  published runs:    $out/archive/demo-stack/
MSG

[ "$night" = 0 ] && [ "$today" = 1 ] && grep -q "router chat.usage_adds_up" "$second/comparison.md" &&
  .venv/bin/python -c 'import json,sys; f=json.load(open(sys.argv[1]))["failures"]; sys.exit(0 if [(x["case_id"], x["layer"], x["confirmed"]) for x in f] == [("chat.usage_adds_up", "router", True)] else 1)' "$second/layers.json"
