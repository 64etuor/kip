#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

SEMANTIC_VENV="${KIP_SEMANTIC_VENV:-$PROJECT_ROOT/var/semantic-venv}"
MODEL_CACHE="${KIP_MODEL_CACHE:-$PROJECT_ROOT/var/model-cache}"

if ! command -v uv >/dev/null 2>&1; then
  printf 'uv is required for the isolated semantic environment: https://docs.astral.sh/uv/\n' >&2
  exit 1
fi

mkdir -p "$MODEL_CACHE"
if [[ ! -x "$SEMANTIC_VENV/bin/python" ]]; then
  uv venv "$SEMANTIC_VENV" --python 3.13
fi

uv pip install \
  --python "$SEMANTIC_VENV/bin/python" \
  'infinity-emb[server,torch]==0.0.77' \
  'click==8.1.8'

"$SEMANTIC_VENV/bin/python" -c 'import infinity_emb, torch; print("infinity-emb ready; torch", torch.__version__)'
printf 'Semantic environment ready: %s\n' "$SEMANTIC_VENV"
printf 'Model cache: %s\n' "$MODEL_CACHE"
