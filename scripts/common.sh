#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PROJECT_ROOT
export KIP_PROJECT_ROOT="${KIP_PROJECT_ROOT:-$PROJECT_ROOT}"
# runtime-path.sh puts scripts/ (and managed runtimes) on PATH exactly once.
# Test fixtures copy common.sh alone; keep the plain fallback for them.
if [[ -f "$PROJECT_ROOT/scripts/runtime-path.sh" ]]; then
  source "$PROJECT_ROOT/scripts/runtime-path.sh"
else
  case ":$PATH:" in
    *":$PROJECT_ROOT/scripts:"*) ;;
    *) export PATH="$PROJECT_ROOT/scripts:$PATH" ;;
  esac
fi

if [[ -f "$PROJECT_ROOT/.env" && "${KIP_SKIP_DOTENV:-0}" != "1" ]]; then
  KIP_DOTENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
  if [[ ! -x "$KIP_DOTENV_PYTHON" ]]; then
    if ! KIP_DOTENV_PYTHON="$(command -v python3 || command -v python)"; then
      printf 'Python is not ready. Run ./scripts/bootstrap.sh before other KIP commands.\n' >&2
      return 69 2>/dev/null || exit 69
    fi
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

# The Compose project name, resolved the way Compose resolves it without -p:
# COMPOSE_PROJECT_NAME (the .env above is already loaded), then the top-level
# `name:` of the Compose file, then the project directory's basename.
kip_compose_project_name() {
  local file="$PROJECT_ROOT/${1:-compose.yaml}" name=""
  if [[ -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
    printf '%s\n' "$COMPOSE_PROJECT_NAME"
    return 0
  fi
  if [[ -f "$file" ]]; then
    name="$(sed -n 's/^name:[[:space:]]*\([^#]*\).*/\1/p' "$file" | head -n 1 | tr -d "\"' \t\r")"
  fi
  if [[ "$name" == *'$'* ]]; then
    # Querying the literal would miss every container of the real project.
    printf 'error: %s sets the Compose project name by interpolation (name: %s), which KIP does not resolve; set COMPOSE_PROJECT_NAME in this deployment'"'"'s .env to the resolved name.\n' "${1:-compose.yaml}" "$name" >&2
    return 2
  fi
  if [[ -z "$name" ]]; then
    name="$(basename "$PROJECT_ROOT" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-' | sed 's/^[_-]*//')"
  fi
  printf '%s\n' "$name"
}

# Compose keys containers and volumes by project name, not by directory, and
# every deployment's compose.yaml names its project `kip`. A second deployment
# on the same machine would reuse or recreate the first one's containers and
# migrate and sync into its database. Returns 0 when the project has no
# containers or only this deployment's (or KIP_COMPOSE_ADOPT=1: the same
# deployment moved), 2 with a message on stderr when a container was created
# from another directory or the name cannot be resolved, and 3 when Docker
# cannot be queried.
kip_compose_project_check() {
  local name dirs dir here other=""
  [[ "${KIP_COMPOSE_ADOPT:-0}" != 1 ]] || return 0
  name="$(kip_compose_project_name "${1:-compose.yaml}")" || return 2
  dirs="$(docker ps --all --filter "label=com.docker.compose.project=$name" \
    --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null)" || return 3
  while IFS= read -r dir; do
    # -ef compares device and inode: a symlinked or differently cased spelling
    # of this directory matches, and a directory that no longer exists does not.
    if [[ -z "$dir" || "$dir" -ef "$PROJECT_ROOT" ]]; then
      continue
    fi
    other="$dir"
    break
  done <<< "$dirs"
  [[ -n "$other" ]] || return 0
  here="$(cd "$PROJECT_ROOT" && pwd -P)"
  cat >&2 <<EOF
error: Docker Compose project "$name" belongs to another deployment at $other
Its containers were created there. This deployment ($here) would reuse or
recreate them and share the ${name}_kip_pgdata, ${name}_kip_cas and
${name}_kip_models volumes, migrating and syncing into that deployment's database.
Fix it one of two ways:
  - A separate deployment: in the new deployment's .env ($here/.env) set
    COMPOSE_PROJECT_NAME=<unique-name>. It gets its own containers and volumes
    and starts with an empty database. To run beside the other deployment it
    also needs free host ports in that .env: KIP_POSTGRES_PORT (change the port
    in KIP_DATABASE_URL to match) and KIP_API_PORT. Keep one model runtime per
    machine: leave KIP_SEMANTIC_PORT on the runtime that is already running.
  - The same deployment moved from that path: run once with KIP_COMPOSE_ADOPT=1
    (for example KIP_COMPOSE_ADOPT=1 ./scripts/app-up.sh --down, then start
    again), or run \`docker compose -p $name down\` from the old location.
    Volumes are kept either way.
EOF
  return 2
}

# Call before any Compose command on the deployment's stack: refuses (2) as
# above, and leaves an unreachable Docker to the Compose command that follows.
kip_compose_project_guard() {
  local status=0
  kip_compose_project_check "$@" || status=$?
  [[ "$status" != 3 ]] || return 0
  return "$status"
}
