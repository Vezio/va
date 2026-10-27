#!/usr/bin/env bash
# Set up .venv with cli-framework and the example tools, httpcheck and modelcheck.
#
#   scripts/install.sh            cli-framework (with S3 and YAML support) and both examples
#   scripts/install.sh --dev      also the development tools: pytest, ruff, mypy, moto
#   scripts/install.sh --recreate throw the virtualenv away first
#
# Needs Python 3.11 or newer: set PYTHON=/path/to/python3 to choose one. Idempotent: rerunning
# is how a dependency change is picked up.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}" EXTRAS="[s3,yaml]" RECREATE=0
for arg in "$@"; do
  case "$arg" in
    --dev) EXTRAS="[dev,s3,yaml]" ;;
    --recreate) RECREATE=1 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "install.sh: unknown option '$arg' (try --help)" >&2; exit 2 ;;
  esac
done

python_ok() { "$1" -c 'import sys; sys.exit(sys.version_info[:2] < (3, 11))' 2>/dev/null; }
if ! python_ok "$PYTHON"; then
  found="$("$PYTHON" -V 2>/dev/null || echo "no $PYTHON on PATH")"
  echo "install.sh: needs Python 3.11 or newer, found: $found (set PYTHON=/path/to/python3)" >&2
  exit 1
fi
# A virtualenv from an older Python, or one created without pip, is replaced or repaired.
if [ -d .venv ] && ! python_ok .venv/bin/python; then
  echo "install.sh: .venv uses an unsupported Python; recreating it"
  RECREATE=1
fi
[ "$RECREATE" = 1 ] && rm -rf .venv
[ -d .venv ] || "$PYTHON" -m venv .venv
.venv/bin/python -m pip --version >/dev/null 2>&1 || .venv/bin/python -m ensurepip --upgrade >/dev/null
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet --editable ".${EXTRAS}" \
  --editable examples/httpcheck --editable examples/modelcheck
.venv/bin/httpcheck profile validate examples/httpcheck/profiles/example.yaml
.venv/bin/modelcheck profile validate examples/modelcheck/profiles/stack.yaml
