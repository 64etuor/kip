#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
output="${1:-$PROJECT_ROOT/var/audits/prerequisites}"
mkdir -p "$output"
work="$(mktemp -d "$output/cold.XXXXXX")"
mkdir -p "$work/project/scripts" "$work/project/requirements" "$work/tools"
for script in prerequisites.sh prerequisites.py runtime-path.sh load_dotenv.py; do
  cp "$SCRIPT_DIR/$script" "$work/project/scripts/$script"
done
cp "$PROJECT_ROOT/requirements/bootstrap.tsv" "$work/project/requirements/"
# Only transfer/extraction utilities are visible. Python, Node and uv must be
# bootstrapped for real; host installations and shell profiles stay untouched.
for name in bash sh dirname uname awk mkdir mktemp curl wget sha256sum shasum tar gzip mv cat ln rm chmod ldd install_name_tool; do
  binary="$(command -v "$name" || true)"
  if [[ -n "$binary" ]]; then ln -s "$binary" "$work/tools/$name"; fi
done
printf 'KIP_ENV=development\nLITERAL="$(this_is_data_not_a_command)"\n' > "$work/project/.env"
env -u KIP_PYTHON -u VIRTUAL_ENV -u PYTHONHOME \
  PATH="$work/tools" UV_CACHE_DIR="$work/cache" \
  "$work/project/scripts/prerequisites.sh" --without-docker
env -u KIP_PYTHON -u VIRTUAL_ENV -u PYTHONHOME \
  PATH="$work/tools" UV_CACHE_DIR="$work/cache" \
  "$work/project/scripts/prerequisites.sh" --check --without-docker
"$work/project/var/runtime/bin/python3" --version
"$work/project/var/runtime/bin/node" --version
printf 'Real cold runtime smoke passed: %s\n' "$work"
