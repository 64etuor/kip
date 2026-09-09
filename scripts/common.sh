#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PROJECT_ROOT
export KIP_PROJECT_ROOT="${KIP_PROJECT_ROOT:-$PROJECT_ROOT}"
export PATH="$PROJECT_ROOT/scripts:$PATH"

if [[ -f "$PROJECT_ROOT/.env" && "${KIP_SKIP_DOTENV:-0}" != "1" ]]; then
  KIP_DOTENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
  if [[ ! -x "$KIP_DOTENV_PYTHON" ]]; then
    KIP_DOTENV_PYTHON="$(command -v python3 || command -v python)"
  fi
  if ! KIP_DOTENV_RECORDS="$($KIP_DOTENV_PYTHON "$PROJECT_ROOT/scripts/load_dotenv.py" "$PROJECT_ROOT/.env")"; then
    return 1 2>/dev/null || exit 1
  fi
  # A setup deployment owns these defaults. Explicit exports and a custom
  # dotenv config still select the operator's requested runtime.
  KIP_DOTENV_CONFIG=""
  while IFS= read -r KIP_DOTENV_RECORD; do
    case "$KIP_DOTENV_RECORD" in KIP_CONFIG=*) KIP_DOTENV_CONFIG="${KIP_DOTENV_RECORD#*=}" ;; esac
  done <<< "$KIP_DOTENV_RECORDS"
  if [[ -z "${KIP_CONFIG+x}" && -f "$PROJECT_ROOT/config/kip.host.generated.toml" ]]; then
    case "$KIP_DOTENV_CONFIG" in
      ""|config/kip.toml|./config/kip.toml|"$PROJECT_ROOT/config/kip.toml")
        export KIP_CONFIG="$PROJECT_ROOT/config/kip.host.generated.toml" ;;
    esac
  fi
  while IFS= read -r KIP_DOTENV_RECORD; do
    [[ -n "$KIP_DOTENV_RECORD" ]] || continue
    KIP_DOTENV_KEY="${KIP_DOTENV_RECORD%%=*}"
    KIP_DOTENV_VALUE="${KIP_DOTENV_RECORD#*=}"
    case "${KIP_CONFIG:-}" in
      */kip.host.generated.toml|*/kip.generated.toml)
        case "$KIP_DOTENV_KEY" in
          KIP_ENV|KIP_WORKSPACE|KIP_ACL_SCOPES|KIP_CAS_PATH|KIP_BACKUP_PATH|KIP_API_HOST|KIP_IDENTITY_MODE|KIP_API_PRINCIPAL_ID|KIP_API_ACL_SCOPES|KIP_JWT_ISSUER|KIP_JWT_AUDIENCE|KIP_JWT_JWKS_URL)
            continue ;;
        esac ;;
    esac
    if [[ -z "${!KIP_DOTENV_KEY+x}" ]]; then
      export "$KIP_DOTENV_KEY=$KIP_DOTENV_VALUE"
    fi
  done <<< "$KIP_DOTENV_RECORDS"
  unset KIP_DOTENV_CONFIG KIP_DOTENV_KEY KIP_DOTENV_PYTHON KIP_DOTENV_RECORD KIP_DOTENV_RECORDS KIP_DOTENV_VALUE
fi

if [[ -z "${KIP_CONFIG+x}" && -f "$PROJECT_ROOT/config/kip.host.generated.toml" ]]; then
  export KIP_CONFIG="$PROJECT_ROOT/config/kip.host.generated.toml"
else
  export KIP_CONFIG="${KIP_CONFIG:-$PROJECT_ROOT/config/kip.toml}"
fi
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
