#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
cd "$PROJECT_ROOT"

fail=0
required() {
  local label="$1" hint="$2" output=""
  shift 2
  if output="$("$@" 2>&1)"; then
    printf '[ok] %s\n' "$label"
  else
    printf '[required missing] %s\n' "$label"
    if [[ -n "$output" ]]; then
      printf '  detail: %s\n' "$(printf '%s' "$output" | head -n 1)"
    fi
    if [[ -n "$hint" ]]; then
      printf '  fix: %s\n' "$hint"
    fi
    fail=1
  fi
}
optional() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then printf '[ok] %s\n' "$label"; else printf '[optional unavailable] %s\n' "$label"; fi
}

node_18_ready() {
  node -e 'const [major] = process.versions.node.split(".").map(Number); process.exit(major >= 18 ? 0 : 1)'
}

kordoc_version_ready() {
  [[ "$(kordoc --version 2>/dev/null)" == "4.7.3" ]]
}

kordoc_ppocr_ready() {
  local status
  status="$(kordoc models --status 2>/dev/null)" || return 1
  KIP_KORDOC_MODEL_STATUS="$status" "$(python_cmd)" -c '
import json
import os

groups = json.loads(os.environ["KIP_KORDOC_MODEL_STATUS"])
ppocr = next((group for group in groups if group.get("group") == "ppocr"), None)
ready = bool(ppocr and ppocr.get("allReady"))
verified = bool(ppocr and all(model.get("verified") for model in ppocr.get("models", [])))
raise SystemExit(0 if ready and verified else 1)
'
}

required "Python 3.12+" \
  "install Python 3.12+ (for example: brew install python@3.12), then run ./scripts/bootstrap.sh to create .venv" \
  "$(python_cmd)" -c 'import sys; raise SystemExit(sys.version_info < (3,12))'
required "root AGENTS.md" \
  "restore AGENTS.md from git (git checkout -- AGENTS.md)" \
  test -f AGENTS.md
required "root CLAUDE.md import" \
  "restore CLAUDE.md so it contains the single line @AGENTS.md" \
  grep -qx '@AGENTS.md' CLAUDE.md
required "configuration ($KIP_CONFIG)" \
  "run ./scripts/bootstrap.sh (it copies config/kip.example.toml to config/kip.toml), or copy it manually: cp config/kip.example.toml config/kip.toml" \
  test -f "$KIP_CONFIG"
optional "Docker" docker version
if command -v docker >/dev/null 2>&1; then
  optional "PostgreSQL container" docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-kip_owner}" -d "${POSTGRES_DB:-kip}"
fi
required "Node 18+ for Kordoc OCR" \
  "install Node.js 18+, then run ./scripts/bootstrap.sh" \
  node_18_ready
required "Kordoc 4.7.3" \
  "run ./scripts/bootstrap.sh to install the exact local Kordoc runtime" \
  kordoc_version_ready
required "Kordoc PP-OCRv5 Korean models" \
  "run ./scripts/install-kordoc.sh to download and verify the Korean OCR model cache" \
  kordoc_ppocr_ready
optional "unhwp command" unhwp --help
optional "Apple osascript" osascript -e 'return 1'
optional "semantic model environment" test -x "$PROJECT_ROOT/var/semantic-venv/bin/infinity_emb"
optional "semantic model server" curl -fsS "http://${KIP_SEMANTIC_HOST:-127.0.0.1}:${KIP_SEMANTIC_PORT:-7997}/models"
if command -v docker >/dev/null 2>&1; then
  optional "pgvector extension" docker compose exec -T postgres psql \
    -U "${POSTGRES_USER:-kip_owner}" -d "${POSTGRES_DB:-kip}" \
    -Atc "SELECT 1 FROM pg_extension WHERE extname='vector'"
fi
required "KIP CLI" \
  "run ./scripts/bootstrap.sh to install the KIP package and its dependencies into .venv" \
  "$SCRIPT_DIR/kip" capabilities
exit "$fail"
