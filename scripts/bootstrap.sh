#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
cd "$PROJECT_ROOT"

if [[ ! -f .env ]]; then
  "$(python_cmd)" "$SCRIPT_DIR/bootstrap_env.py" "$PROJECT_ROOT"
fi
if [[ ! -f config/kip.toml ]]; then
  cp config/kip.example.toml config/kip.toml
fi
version_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null
}
if [[ -x .venv/bin/python ]] && ! version_ok .venv/bin/python; then
  echo "error: .venv uses Python < 3.12. Remove it (rm -rf .venv) and re-run ./scripts/bootstrap.sh with Python 3.12+." >&2
  exit 1
fi
if [[ ! -d .venv ]]; then
  PYTHON_BIN="${KIP_PYTHON:-python3}"
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "error: python3 not found. Install Python 3.12+ (for example: brew install python@3.12) and re-run ./scripts/bootstrap.sh" >&2
    exit 1
  fi
  if ! version_ok "$PYTHON_BIN"; then
    detected="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    echo "error: KIP requires Python >= 3.12 but $PYTHON_BIN is $detected." >&2
    echo "Install Python 3.12+ (for example: brew install python@3.12) or set KIP_PYTHON=/path/to/python3.12 and re-run ./scripts/bootstrap.sh" >&2
    exit 1
  fi
  "$PYTHON_BIN" -m venv .venv
fi
. .venv/bin/activate
if command -v uv >/dev/null 2>&1; then
  KIP_BOOTSTRAP_UV="$(command -v uv)"
else
  # Keep the bootstrap tool outside .venv: uv sync removes undeclared packages
  # from that environment. Pin the fallback tool to the same version as CI.
  KIP_BOOTSTRAP_UV_HOME="$PROJECT_ROOT/var/bootstrap-uv-0.8.22"
  if [[ ! -x "$KIP_BOOTSTRAP_UV_HOME/bin/uv" ]]; then
    python -m venv "$KIP_BOOTSTRAP_UV_HOME"
    "$KIP_BOOTSTRAP_UV_HOME/bin/python" -m pip install --disable-pip-version-check --no-deps 'uv==0.8.22'
  fi
  KIP_BOOTSTRAP_UV="$KIP_BOOTSTRAP_UV_HOME/bin/uv"
fi
"$KIP_BOOTSTRAP_UV" sync --frozen --python "$PROJECT_ROOT/.venv/bin/python" \
  --extra postgres --extra api --extra identity --extra extractors \
  --extra mcp --extra telemetry --extra dev
"$SCRIPT_DIR/install-kordoc.sh"
mkdir -p var/cas var/backups var/log
python scripts/create_sample_xlsx.py
python scripts/generate_contracts.py
printf 'Bootstrap complete. Next: ./scripts/kip setup inspect (guided deployment); ./scripts/dev-up.sh && ./scripts/migrate.sh (local sample).\n'
