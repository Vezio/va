#!/usr/bin/env bash
# new-app.sh: start a new tool, ready to develop, test, containerise and deploy.
#
#   scripts/new-app.sh NAME DEST            depend on this commit of VA on GitHub
#   scripts/new-app.sh --local NAME DEST    depend on this checkout (for framework development)
#
# Creates DEST with a copy of the example tool renamed to NAME (package, command and NAME_*
# variables), plus its own README, tests, container image definition, Kubernetes manifests and
# CI workflow. NAME is a lowercase Python identifier, e.g. mytool.
#
# By default the tool depends on the current commit of this checkout, as a source archive from
# the public repository, so every machine and image build installs the same code without git.
# The commit must already be pushed. With --local, the tool uses this checkout in editable mode
# instead, so framework changes take effect without reinstalling; this works only on this
# machine.
set -euo pipefail

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }
local_dep=0
if [ "${1:-}" = "--local" ]; then local_dep=1; shift; fi
case "${1:-}" in -h | --help) usage; exit 0 ;; esac
name="${1:-}"
dest="${2:-}"
if [ -z "$name" ] || [ -z "$dest" ]; then usage >&2; exit 2; fi
[[ "$name" =~ ^[a-z][a-z0-9_]*$ ]] || {
  echo "new-app.sh: NAME must be a lowercase identifier, e.g. mytool" >&2; exit 2; }
[ -e "$dest" ] && { echo "new-app.sh: $dest already exists" >&2; exit 2; }

root="$(cd "$(dirname "$0")/.." && pwd)"
upper="$(printf '%s' "$name" | tr '[:lower:]' '[:upper:]')"
repo_url="https://github.com/Vezio/va"

# Select the VA dependency.
if [ "$local_dep" = 1 ]; then
  # A file:// URL is not used because pip caches the wheel built from it and reuses that copy
  # after the checkout changes. The editable install in $install satisfies this requirement.
  framework="va-framework[s3,yaml]"
  pinned="the checkout at $root, installed in editable mode"
  install=".venv/bin/pip install -e \"${root}[s3]\" -e \".[dev]\""
else
  commit="$(git -C "$root" rev-parse HEAD)"
  if ! git -C "$root" branch -r --contains "$commit" 2>/dev/null | grep -q .; then
    echo "new-app.sh: commit ${commit:0:12} is not on the remote yet; push it first, or use --local" >&2
    exit 2
  fi
  if [ -n "$(git -C "$root" status --porcelain -- src examples)" ]; then
    echo "new-app.sh: warning: uncommitted changes in src/ or examples/ are not in commit ${commit:0:12}" >&2
  fi
  framework="va-framework[s3,yaml] @ $repo_url/archive/$commit.tar.gz"
  install=".venv/bin/pip install -e \".[dev]\""
  pinned="commit ${commit:0:12}"
fi

rename() {  # the example's names, in every text file under the given paths
  find "$@" -type f \( -name '*.py' -o -name '*.toml' -o -name '*.md' -o -name '*.yaml' \
    -o -name '*.sh' -o -name '*.example' \) -print0 |
    xargs -0 sed -i.bak -e "s/httpcheck/$name/g" -e "s/HTTPCHECK/$upper/g"
  find "$@" -name '*.bak' -delete
}

# Copy the example tool: code, profiles and tests.
mkdir -p "$dest"
cp -R "$root/examples/httpcheck/src" "$root/examples/httpcheck/tests" "$root/examples/httpcheck/profiles" "$dest/"
find "$dest" \( -name '__pycache__' -o -name '*.egg-info' \) -prune -exec rm -rf {} +
mv "$dest/src/httpcheck" "$dest/src/$name"
mv "$dest/tests/test_httpcheck.py" "$dest/tests/test_$name.py"
rename "$dest"

cat >"$dest/pyproject.toml" <<EOF
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "$name"
dynamic = ["version"]
description = "Checks for a deployed system, built on VA"
requires-python = ">=3.11"
# VA, pinned to one commit. To upgrade: change the commit, reinstall, rerun
# scripts/lock.sh and the tests.
dependencies = ["$framework"]

[project.optional-dependencies]
dev = ["pytest>=8", "ruff>=0.8"]

[project.scripts]
$name = "$name.cli:main"

[tool.setuptools.dynamic]
version = {attr = "$name.__version__"}

[tool.setuptools.packages.find]
where = ["src"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
# T20 bans print(); output goes through VA's logging (log.emit, get_logger).
# D enforces Google-style docstrings; constructor arguments go in the class docstring.
select = ["E", "F", "W", "I", "T20", "B", "UP", "D"]
ignore = ["D107"]

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.pytest.ini_options]
testpaths = ["tests"]
EOF

# Container image: dependencies from a hash-checked lock file, then the tool.
mkdir -p "$dest/container" "$dest/scripts"
cat >"$dest/container/Containerfile" <<EOF
# The $name runner image. Build from the repository root, after scripts/lock.sh:
#   podman build -f container/Containerfile -t <registry>/<org>/$name:<sha> .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HOME=/tmp
WORKDIR /src
# All dependencies, including VA, are pinned with hashes so that rebuilds are reproducible
# and packages with unexpected contents are rejected.
COPY container/requirements.lock ./
RUN pip install --require-hashes -r requirements.lock
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-deps . && pip check && rm -rf /src
USER 65532:65532
WORKDIR /work
ENTRYPOINT ["$name"]
CMD ["job"]
EOF
cat >"$dest/scripts/lock.sh" <<'EOF'
#!/usr/bin/env bash
# lock.sh: pin every dependency of the image, with hashes, into container/requirements.lock.
# Rerun after changing the dependencies in pyproject.toml and review the diff. Requires uv.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo "lock.sh: needs uv (https://docs.astral.sh/uv/)" >&2; exit 2; }
uv pip compile pyproject.toml --generate-hashes --python-version 3.12 \
  --python-platform x86_64-manylinux_2_28 --no-header --quiet -o container/requirements.lock
echo "wrote container/requirements.lock"
EOF
chmod +x "$dest/scripts/lock.sh"

# Kubernetes: the CronJob base, an example overlay and the trigger script.
mkdir -p "$dest/deploy"
cp -R "$root/deploy/base" "$root/deploy/overlays" "$root/deploy/trigger.sh" \
  "$root/deploy/secrets.env.example" "$dest/deploy/"
sed -e "s#(\.\./docs/deploying\.md)#($repo_url/blob/main/docs/deploying.md)#" \
  "$root/deploy/README.md" >"$dest/deploy/README.md"
rename "$dest/deploy"

# Continuous integration workflow.
mkdir -p "$dest/.github/workflows"
cat >"$dest/.github/workflows/tests.yaml" <<'EOF'
name: Tests
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: ruff check . && ruff format --check .
      - run: python -m pytest -q
EOF

cat >"$dest/.gitignore" <<'EOF'
.venv/
__pycache__/
*.egg-info/
runs/
*.local.toml
*.local.yaml
*.local.json
secrets.env
EOF

cat >"$dest/README.md" <<EOF
# $name

\`$name\` verifies a deployed system. It is built on [VA]($repo_url) ($pinned).

## Getting started

Requirements: Python 3.11 or later.

\`\`\`sh
python3 -m venv .venv && $install
.venv/bin/python -m pytest -q
.venv/bin/$name profile show profiles/example.yaml
GATEWAY_URL=https://... GATEWAY_TOKEN=... BACKEND_URL=https://... \\
  .venv/bin/$name run --profile profiles/example.yaml --html
\`\`\`

## Project layout

| Path | Purpose |
|---|---|
| \`src/$name/registry.py\` | the case registry, \`REGISTRY\` |
| \`src/$name/cases/\` | the checks; a new module must be imported in \`cases/__init__.py\` and listed in \`__all__\` |
| \`src/$name/profile.py\` | the profile schema and its validation |
| \`src/$name/client.py\` | the recording HTTP client used by the checks |
| \`src/$name/cli.py\` | the \`run\` and \`profile\` commands; \`job\`, \`publish\`, \`compare\` and \`notify\` are provided by the framework |
| \`profiles/\` | profiles; these name environment variables and never contain addresses or credentials |
| \`tests/\` | a simulated service with selectable defects; each check is proven to fail against one |
| \`container/\` | the runner image definition; run \`scripts/lock.sh\` to pin its dependencies before building |
| \`deploy/\` | a Kubernetes CronJob running \`$name job\`, an example overlay and \`trigger.sh\` |

## Documentation

Writing checks, designing profiles, operation and deployment are covered by the VA
guides: $repo_url/tree/main/docs
EOF

echo "created $dest: the tool $name, on VA $pinned"
echo "next: cd $dest && python3 -m venv .venv && $install && .venv/bin/python -m pytest -q"
