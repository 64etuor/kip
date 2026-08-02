#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PROJECT_ROOT
export KIP_PROJECT_ROOT="${KIP_PROJECT_ROOT:-$PROJECT_ROOT}"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi

export KIP_CONFIG="${KIP_CONFIG:-$PROJECT_ROOT/config/kip.toml}"
export PYTHONPATH="${PYTHONPATH:-$PROJECT_ROOT/src}"

python_cmd() {
  if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    printf '%s\n' "$PROJECT_ROOT/.venv/bin/python"
  else
    command -v python3 || command -v python
  fi
}

kip_cmd() {
  if [[ -x "$PROJECT_ROOT/.venv/bin/kip" ]]; then
    printf '%s\n' "$PROJECT_ROOT/.venv/bin/kip"
  elif command -v kip >/dev/null 2>&1; then
    command -v kip
  else
    printf '%s -m kip.cli\n' "$(python_cmd)"
  fi
}
