#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prerequisites deliberately precede common.sh: parsing an existing .env also
# needs Python, and a new machine may have none yet.
"$SCRIPT_DIR/prerequisites.sh" "$@"
for arg in "$@"; do
  if [[ "$arg" == "--check" || "$arg" == "--help" || "$arg" == "-h" ]]; then exit 0; fi
done
source "$SCRIPT_DIR/common.sh"
cd "$PROJECT_ROOT"

version_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null
}
if [[ -e .venv || -L .venv ]] && ! version_ok "$PROJECT_ROOT/.venv/bin/python"; then
  echo "error: existing .venv is not usable with Python 3.12+. Preserve it under another name and rerun bootstrap; it has not been deleted." >&2
  exit 1
fi
if [[ ! -d .venv ]]; then
  PYTHON_BIN="${KIP_PYTHON:-$(command -v python3)}"
  if ! version_ok "$PYTHON_BIN"; then
    echo "error: KIP_PYTHON must select Python 3.12+; correct the configured path and retry." >&2
    exit 1
  fi
  uv venv --python "$PYTHON_BIN" "$PROJECT_ROOT/.venv"
fi
if [[ ! -f .env ]]; then
  "$(python_cmd)" "$SCRIPT_DIR/bootstrap_env.py" "$PROJECT_ROOT"
fi
if [[ ! -f config/kip.toml ]]; then
  cp config/kip.example.toml config/kip.toml
fi
UV_PROJECT_ENVIRONMENT="$PROJECT_ROOT/.venv" uv sync --frozen --python "$PROJECT_ROOT/.venv/bin/python" \
  --extra postgres --extra api --extra identity --extra extractors \
  --extra mcp --extra telemetry --extra dev
"$SCRIPT_DIR/install-kordoc.sh"
mkdir -p var/cas var/backups var/log
"$(python_cmd)" scripts/create_sample_xlsx.py
"$(python_cmd)" scripts/generate_contracts.py
printf 'Bootstrap complete. Next: ./scripts/kip setup inspect (guided deployment); ./scripts/app-up.sh --database-only (local sample).\n'
