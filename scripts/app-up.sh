#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
cd "$PROJECT_ROOT"

usage() {
  cat >&2 <<'EOF'
Usage: app-up.sh [--down]

Starts the KIP application profile (api, worker, migrate, postgres).
When guided setup has been applied (compose.generated.yaml plus
config/kip.generated.toml exist), the generated override is layered on top of
compose.yaml so the approved source mounts, CAS path, and generated runtime
configuration take effect. Otherwise the plain app profile is started with the
baked-in container configuration.

  --down    Stop the app profile started by this script.
EOF
}

compose_args=(-f compose.yaml)
if [[ -f compose.generated.yaml && -f config/kip.generated.toml ]]; then
  compose_args+=(-f compose.generated.yaml)
  using_generated=1
else
  using_generated=0
fi

case "${1:-}" in
  --down)
    docker compose "${compose_args[@]}" --profile app down
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
  echo "Using generated setup override: compose.yaml + compose.generated.yaml"
else
  cat >&2 <<'EOF'
notice: no generated setup override found (compose.generated.yaml and
config/kip.generated.toml). Starting the plain app profile with the baked-in
container configuration. Run the guided setup (kip setup ...) and re-run this
script to apply approved source mounts and configuration.
EOF
fi

docker compose "${compose_args[@]}" --profile app up -d --build
echo "App profile is starting. Check http://127.0.0.1:8080/healthz once healthy."
if [[ "$using_generated" == "1" ]]; then
  echo "Next: ./scripts/kip sync run --source SOURCE && ./scripts/kip search \"query\" --limit 5"
fi
