#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PROJECT_ROOT
export KIP_PROJECT_ROOT="${KIP_PROJECT_ROOT:-$PROJECT_ROOT}"

if [[ -f "$PROJECT_ROOT/.env" && "${KIP_SKIP_DOTENV:-0}" != "1" ]]; then
  KIP_DOTENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
  if [[ ! -x "$KIP_DOTENV_PYTHON" ]]; then
    KIP_DOTENV_PYTHON="$(command -v python3 || command -v python)"
  fi
  if ! KIP_DOTENV_RECORDS="$($KIP_DOTENV_PYTHON "$PROJECT_ROOT/scripts/load_dotenv.py" "$PROJECT_ROOT/.env")"; then
    return 1 2>/dev/null || exit 1
  fi
  while IFS= read -r KIP_DOTENV_RECORD; do
    [[ -n "$KIP_DOTENV_RECORD" ]] || continue
    KIP_DOTENV_KEY="${KIP_DOTENV_RECORD%%=*}"
    KIP_DOTENV_VALUE="${KIP_DOTENV_RECORD#*=}"
    if [[ -z "${!KIP_DOTENV_KEY+x}" ]]; then
      export "$KIP_DOTENV_KEY=$KIP_DOTENV_VALUE"
    fi
  done <<< "$KIP_DOTENV_RECORDS"
  unset KIP_DOTENV_KEY KIP_DOTENV_PYTHON KIP_DOTENV_RECORD KIP_DOTENV_RECORDS KIP_DOTENV_VALUE
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
