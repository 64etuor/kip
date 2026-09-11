#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
cd "$PROJECT_ROOT"

usage() {
  cat >&2 <<'EOF'
Usage: app-up.sh [--database-only | --down]

Starts the KIP application profile (api, worker, migrate, postgres).
When guided setup has been applied (compose.generated.yaml plus
config/kip.generated.toml exist), the standalone generated Compose project applies
the approved source mounts, CAS path, and generated runtime
configuration. Otherwise the plain app profile is started with the
baked-in container configuration.

  --database-only  Prepare PostgreSQL and host migrations for CLI/MCP; no app build.
  --down           Stop the app profile started by this script.
EOF
}

if [[ "$#" -gt 1 ]]; then
  usage
  exit 2
fi

if [[ -f compose.generated.yaml && -f config/kip.generated.toml ]]; then
  using_generated=1
elif [[ -e compose.generated.yaml || -e config/kip.generated.toml ]]; then
  echo "error: incomplete generated setup; regenerate and apply a setup plan." >&2
  exit 1
else
  using_generated=0
fi

run_compose() {
  if [[ "$using_generated" == "1" ]]; then
    "$(python_cmd)" "$SCRIPT_DIR/setup_compose.py" "$@"
  else
    docker compose -f compose.yaml --profile app ${KIP_EXTRA_PROFILE:+--profile "$KIP_EXTRA_PROFILE"} "$@"
  fi
}

total_memory_kib() {
  # Integer KiB; a separate function keeps macOS bash 3.2 from misparsing a
  # case statement inside a command substitution.
  case "$(uname -s)" in
    Darwin) echo $(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1024 )) ;;
    Linux) awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || echo 0 ;;
    *) echo 0 ;;
  esac
}

start_semantic_server() {
  # The model runtime is a host process on loopback; containers use the
  # compose `models` service instead. Opted-out or not installed: skip.
  [[ "${KIP_SEMANTIC:-on}" != off && -x "$PROJECT_ROOT/var/semantic-venv/bin/infinity_emb" ]] || return 0
  if "$SCRIPT_DIR/semantic-server.sh" start && "$SCRIPT_DIR/semantic-server.sh" wait; then
    return 0
  fi
  echo "warning: the semantic model server is not ready; search uses the lexical path or skips the reranker until the model runtime answers (see the message above for the fix)." >&2
}

case "${1:-}" in
  --database-only)
    if [[ "$using_generated" == "1" ]]; then
      run_compose --database-only
    else
      docker compose -f compose.yaml up -d --wait --wait-timeout 60 postgres
      "$SCRIPT_DIR/migrate.sh"
    fi
    start_semantic_server
    echo "Database ready and migrations complete."
    echo 'Next: ./scripts/kip capabilities; ./scripts/kip sync run --source SOURCE; ./scripts/kip search "query" --limit 5'
    echo 'For the API and worker, run ./scripts/app-up.sh.'
    exit 0
    ;;
  --down)
    run_compose down
    exit 0
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  "")
    ;;
  *)
    usage
    exit 2
    ;;
esac

if [[ "$using_generated" == "1" ]]; then
  echo "Using approved standalone setup: compose.generated.yaml"
else
  cat >&2 <<'EOF'
notice: no generated setup override found (compose.generated.yaml and
config/kip.generated.toml). Starting the plain app profile with the baked-in
container configuration. Run the guided setup (kip setup ...) and re-run this
script to apply approved source mounts and configuration.
EOF
fi

host_runtime_running() {
  # This checkout's host runtime (semantic-server.sh start, launchd or
  # systemd `run`), counted even while it is still loading the models.
  [[ -x "$PROJECT_ROOT/var/semantic-venv/bin/infinity_emb" ]] \
    && "$SCRIPT_DIR/semantic-server.sh" status >/dev/null 2>&1
}

compose_models_running() {
  [[ -n "$(KIP_EXTRA_PROFILE=semantic run_compose ps -q --status running models 2>/dev/null)" ]]
}

# Exactly one model runtime per machine (ADR-065). A host runtime of this
# checkout serves the machine whenever it runs, and the containers fall back
# to lexical search. Otherwise on amd64 the compose `models` service serves the
# containers and, published on loopback, the host CLI/MCP too; on ARM (the
# pinned image is amd64-only) the native host runtime is started.
semantic_runtime_note=""
host_runtime_note="Semantic search: the host model runtime serves CLI/MCP; the API and worker containers use lexical search (they report semantic_degraded). On an amd64 host without a running host runtime the compose models service is used instead."
if [[ "${KIP_SEMANTIC:-on}" != off ]]; then
  memory_kib="$(total_memory_kib)"
  if (( memory_kib > 0 && memory_kib < 8 * 1024 * 1024 )); then
    semantic_runtime_note="Semantic search: this machine has under 8 GiB of RAM; no model runtime was started and search stays lexical."
  elif host_runtime_running; then
    start_semantic_server
    semantic_runtime_note="$host_runtime_note"
  elif [[ "$(uname -m)" =~ ^(x86_64|amd64)$ ]]; then
    if ! curl -fsS --max-time 5 "http://127.0.0.1:${KIP_SEMANTIC_PORT:-7997}/models" >/dev/null 2>&1 || compose_models_running; then
      # Keep the profile when `models` already answers so an upgrade
      # recreates it with the new image and command.
      export KIP_EXTRA_PROFILE=semantic
      semantic_runtime_note="Semantic search: the compose models service serves the API and worker containers and, on 127.0.0.1:${KIP_SEMANTIC_PORT:-7997}, the host CLI/MCP."
    else
      # A runtime this script does not manage answers on the port; a compose
      # `models` service could not bind it.
      semantic_runtime_note="Semantic search: an existing model runtime on 127.0.0.1:${KIP_SEMANTIC_PORT:-7997} serves CLI/MCP; the API and worker containers use lexical search (they report semantic_degraded)."
    fi
  else
    start_semantic_server
    semantic_runtime_note="$host_runtime_note"
  fi
fi
run_compose up -d --build
[[ -z "$semantic_runtime_note" ]] || echo "$semantic_runtime_note"
echo "App profile is starting. Check http://127.0.0.1:${KIP_API_PORT:-8080}/readyz once healthy."
if [[ "$using_generated" == "1" ]]; then
  echo "Next: ./scripts/kip sync run --source SOURCE && ./scripts/kip search \"query\" --limit 5"
fi
