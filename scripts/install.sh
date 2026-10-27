#!/usr/bin/env bash
# Set up .venv with cli_framework and the example app.
#
#   scripts/install.sh            cli_framework + httpcheck
#   scripts/install.sh --dev      also ruff, pytest and the s3/yaml extras
#   scripts/install.sh --recreate throw the virtualenv away first
#
# Idempotent: re-running is how a dependency change is picked up.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}" EXTRAS="" RECREATE=0
for arg in "$@"; do
  case "$arg" in
    --dev) EXTRAS="[dev,s3,yaml]" ;;
    --recreate) RECREATE=1 ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "install.sh: unknown option '$arg'" >&2; exit 2 ;;
  esac
done
"$PYTHON" -c 'import sys; sys.exit(sys.version_info[:2] < (3, 11))' || {
  echo "install.sh: need Python >= 3.11 (set PYTHON=...)" >&2; exit 1; }
[ "$RECREATE" = 1 ] && rm -rf .venv
[ -d .venv ] || "$PYTHON" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet --editable ".${EXTRAS}" --editable examples/httpcheck
.venv/bin/httpcheck --version
.venv/bin/httpcheck profile validate examples/httpcheck/profiles/example.toml
