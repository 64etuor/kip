#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prerequisites deliberately precede common.sh: parsing an existing .env also
# needs Python, and a new machine may have none yet.
"$SCRIPT_DIR/prerequisites.sh" "$@"
for arg in "$@"; do
  if [[ "$arg" == "--check" || "$arg" == "--help" || "$arg" == "-h" ]]; then exit 0; fi
done
source "$SCRIPT_DIR/common.sh"
cd "$PROJECT_ROOT"

version_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null
}
if [[ -e .venv || -L .venv ]] && ! version_ok "$PROJECT_ROOT/.venv/bin/python"; then
  echo "error: existing .venv is not usable with Python 3.12+. Preserve it under another name and rerun bootstrap; it has not been deleted." >&2
  exit 1
fi
if [[ ! -d .venv ]]; then
  PYTHON_BIN="${KIP_PYTHON:-$(command -v python3)}"
  if ! version_ok "$PYTHON_BIN"; then
    echo "error: KIP_PYTHON must select Python 3.12+; correct the configured path and retry." >&2
    exit 1
  fi
  uv venv --python "$PYTHON_BIN" "$PROJECT_ROOT/.venv"
fi
if [[ ! -f .env ]]; then
  "$(python_cmd)" "$SCRIPT_DIR/bootstrap_env.py" "$PROJECT_ROOT"
fi
created_config=0
if [[ ! -f config/kip.toml ]]; then
  cp config/kip.example.toml config/kip.toml
  created_config=1
fi
UV_PROJECT_ENVIRONMENT="$PROJECT_ROOT/.venv" uv sync --frozen --python "$PROJECT_ROOT/.venv/bin/python" \
  --extra postgres --extra api --extra identity --extra extractors \
  --extra mcp --extra telemetry --extra dev
"$SCRIPT_DIR/install-kordoc.sh"
mkdir -p var/cas var/backups var/log

# Semantic search (hybrid lexical+vector retrieval) is on by default
# (ADR-065): an isolated model runtime plus the pinned embedding snapshot; the
# BGE reranker is opt-in (KIP_SEMANTIC_RERANKER=on). KIP_SEMANTIC=off
# (environment or .env) keeps a lexical-only install. A failure here never
# fails bootstrap: search stays lexical and `kip doctor` explains how to finish.
total_memory_kib() {
  # Integer KiB so shell arithmetic never sees awk's exponent notation.
  case "$(uname -s)" in
    Darwin) echo $(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1024 )) ;;
    Linux) awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || echo 0 ;;
    *) echo 0 ;;
  esac
}
semantic_setting="${KIP_SEMANTIC:-on}"
semantic_ready=0
if [[ "$semantic_setting" == off ]]; then
  grep -q '^KIP_SEMANTIC=' .env || printf 'KIP_SEMANTIC=off\n' >> .env
  printf 'Semantic search: skipped (KIP_SEMANTIC=off); search stays lexical.\n'
elif memory_kib="$(total_memory_kib)" && (( memory_kib > 0 && memory_kib < 8 * 1024 * 1024 )); then
  printf 'Semantic search: skipped; the embedding model runtime needs at least 8 GiB of RAM. Search stays lexical.\n'
elif "$SCRIPT_DIR/bootstrap-semantic.sh" && "$SCRIPT_DIR/semantic-server.sh" prefetch; then
  semantic_ready=1
  printf 'Semantic search: model runtime and pinned models are ready (./scripts/app-up.sh starts the server).\n'
else
  printf 'Semantic search: the model runtime could not be installed; search stays lexical. Retry: ./scripts/bootstrap-semantic.sh && ./scripts/semantic-server.sh prefetch\n' >&2
fi
if [[ "$created_config" == 1 && "$semantic_ready" == 0 ]]; then
  # A lexical-only machine should not warn on every query.
  sed -i.bak 's/^semantic_enabled = true$/semantic_enabled = false/' config/kip.toml && rm -f config/kip.toml.bak
fi
"$(python_cmd)" scripts/create_sample_xlsx.py
"$(python_cmd)" scripts/generate_contracts.py
printf 'Bootstrap complete. Next: ./scripts/kip setup inspect (guided deployment); ./scripts/app-up.sh --database-only (local sample).\n'
