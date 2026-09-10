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
    docker compose -f compose.yaml --profile app "$@"
  fi
}

case "${1:-}" in
  --database-only)
    if [[ "$using_generated" == "1" ]]; then
      run_compose --database-only
    else
      docker compose -f compose.yaml up -d --wait --wait-timeout 60 postgres
      "$SCRIPT_DIR/migrate.sh"
    fi
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

run_compose up -d --build
echo "App profile is starting. Check http://127.0.0.1:${KIP_API_PORT:-8080}/readyz once healthy."
if [[ "$using_generated" == "1" ]]; then
  echo "Next: ./scripts/kip sync run --source SOURCE && ./scripts/kip search \"query\" --limit 5"
fi
