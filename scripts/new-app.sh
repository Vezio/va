#!/usr/bin/env bash
# new-app.sh: start a new tool from the example app.
#
#   scripts/new-app.sh NAME DEST
#
# Copies examples/httpcheck to DEST, renamed to NAME (package, command, HTTPCHECK_* variables),
# with runkit as a dependency. Then: replace the cases and the profile with your own, keep the
# fake server pattern, and prove every case fails against its defect.
set -euo pipefail
name="${1:?usage: new-app.sh NAME DEST}"
dest="${2:?usage: new-app.sh NAME DEST}"
[[ "$name" =~ ^[a-z][a-z0-9_]*$ ]] || { echo "new-app.sh: NAME must be a lowercase identifier" >&2; exit 2; }
[ -e "$dest" ] && { echo "new-app.sh: $dest already exists" >&2; exit 2; }
root="$(cd "$(dirname "$0")/.." && pwd)"
upper="$(printf '%s' "$name" | tr '[:lower:]' '[:upper:]')"
mkdir -p "$dest"
cp -R "$root/examples/httpcheck/." "$dest/"
find "$dest" -name '__pycache__' -prune -exec rm -rf {} + -o -name '*.egg-info' -prune -exec rm -rf {} +
mv "$dest/src/httpcheck" "$dest/src/$name"
mv "$dest/tests/test_httpcheck.py" "$dest/tests/test_$name.py"
find "$dest" -type f \( -name '*.py' -o -name '*.toml' -o -name '*.md' \) -print0 |
  xargs -0 sed -i.bak -e "s/httpcheck/$name/g" -e "s/HTTPCHECK/$upper/g"
find "$dest" -name '*.bak' -delete
# The new tool lives outside this repository: depend on runkit by path (pin a tag in real use).
sed -i.bak -e "s#dependencies = \[\"runkit>=0.2\"\]#dependencies = [\"runkit @ file://$root\"]#" \
  -e 's#^extend = .*#line-length = 100#' "$dest/pyproject.toml" && rm "$dest/pyproject.toml.bak"
echo "created $dest: cd $dest && python3 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/$name --help"
