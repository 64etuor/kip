#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
cd "$PROJECT_ROOT"

fail=0
required() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then printf '[ok] %s\n' "$label"; else printf '[required missing] %s\n' "$label"; fail=1; fi
}
optional() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then printf '[ok] %s\n' "$label"; else printf '[optional unavailable] %s\n' "$label"; fi
}

required "Python 3.12+" "$(python_cmd)" -c 'import sys; raise SystemExit(sys.version_info < (3,12))'
required "root AGENTS.md" test -f AGENTS.md
required "root CLAUDE.md import" grep -qx '@AGENTS.md' CLAUDE.md
required "configuration" test -f "$KIP_CONFIG"
optional "Docker" docker version
if command -v docker >/dev/null 2>&1; then
  optional "PostgreSQL container" docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-kip_owner}" -d "${POSTGRES_DB:-kip}"
fi
optional "Node/npx for optional kordoc adapter" npx --version
optional "unhwp command" unhwp --help
optional "Apple osascript" osascript -e 'return 1'
optional "semantic model environment" test -x "$PROJECT_ROOT/var/semantic-venv/bin/infinity_emb"
optional "semantic model server" curl -fsS "http://${KIP_SEMANTIC_HOST:-127.0.0.1}:${KIP_SEMANTIC_PORT:-7997}/models"
if command -v docker >/dev/null 2>&1; then
  optional "pgvector extension" docker compose exec -T postgres psql \
    -U "${POSTGRES_USER:-kip_owner}" -d "${POSTGRES_DB:-kip}" \
    -Atc "SELECT 1 FROM pg_extension WHERE extname='vector'"
fi
required "KIP CLI" "$SCRIPT_DIR/kip" capabilities
exit "$fail"
