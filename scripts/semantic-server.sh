#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

SEMANTIC_VENV="${KIP_SEMANTIC_VENV:-$PROJECT_ROOT/var/semantic-venv}"
MODEL_CACHE="${KIP_MODEL_CACHE:-$PROJECT_ROOT/var/model-cache}"
RUN_DIR="$PROJECT_ROOT/var/run"
LOG_DIR="$PROJECT_ROOT/var/log"
PID_FILE="$RUN_DIR/semantic-server.pid"
LOG_FILE="$LOG_DIR/semantic-server.log"
HOST="${KIP_SEMANTIC_HOST:-127.0.0.1}"
PORT="${KIP_SEMANTIC_PORT:-7997}"
DEVICE="${KIP_SEMANTIC_DEVICE:-mps}"
EMBED_MODEL="${KIP_EMBEDDING_MODEL:-Qwen/Qwen3-Embedding-0.6B}"
EMBED_REVISION="${KIP_EMBEDDING_REVISION:-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3}"
EMBED_SERVED="${KIP_EMBEDDING_SERVED_MODEL:-kip-qwen3-embedding-0.6b}"
RERANK_MODEL="${KIP_RERANKER_MODEL:-BAAI/bge-reranker-v2-m3}"
RERANK_REVISION="${KIP_RERANKER_REVISION:-953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e}"
RERANK_SERVED="${KIP_RERANKER_SERVED_MODEL:-kip-bge-reranker-v2-m3}"
EMBED_BATCH_SIZE="${KIP_EMBEDDING_SERVER_BATCH_SIZE:-4}"
RERANK_BATCH_SIZE="${KIP_RERANKER_SERVER_BATCH_SIZE:-2}"

if [[ "$HOST" != "127.0.0.1" && "$HOST" != "::1" && "$HOST" != "localhost" ]]; then
  printf 'KIP semantic server must bind to a loopback host: %s\n' "$HOST" >&2
  exit 1
fi
if [[ ! -x "$SEMANTIC_VENV/bin/infinity_emb" ]]; then
  printf 'Run ./scripts/bootstrap-semantic.sh first.\n' >&2
  exit 1
fi

mkdir -p "$MODEL_CACHE" "$RUN_DIR" "$LOG_DIR"
export HF_HOME="$MODEL_CACHE"
export SENTENCE_TRANSFORMERS_HOME="$MODEL_CACHE/sentence-transformers"
export DO_NOT_TRACK=1
export HF_HUB_DISABLE_TELEMETRY=1

command_line=(
  "$SEMANTIC_VENV/bin/infinity_emb" v2
  --host "$HOST"
  --port "$PORT"
  --model-id "$EMBED_MODEL"
  --model-id "$RERANK_MODEL"
  --revision "$EMBED_REVISION"
  --revision "$RERANK_REVISION"
  --served-model-name "$EMBED_SERVED"
  --served-model-name "$RERANK_SERVED"
  --batch-size "$EMBED_BATCH_SIZE"
  --batch-size "$RERANK_BATCH_SIZE"
  --engine torch
  --device "$DEVICE"
  --no-bettertransformer
)

running_pid() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi
  local pid
  pid="$(<"$PID_FILE")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" >/dev/null 2>&1 || return 1
  ps -p "$pid" -o command= | grep -F "$SEMANTIC_VENV" >/dev/null
}

action="${1:-run}"
case "$action" in
  run)
    exec "${command_line[@]}"
    ;;
  start)
    if running_pid; then
      printf 'Semantic server already running with PID %s\n' "$(<"$PID_FILE")"
      exit 0
    fi
    nohup "${command_line[@]}" >>"$LOG_FILE" 2>&1 &
    pid="$!"
    printf '%s\n' "$pid" >"$PID_FILE"
    printf 'Semantic server starting with PID %s; log: %s\n' "$pid" "$LOG_FILE"
    ;;
  stop)
    if ! running_pid; then
      printf 'Semantic server is not running.\n'
      exit 0
    fi
    pid="$(<"$PID_FILE")"
    kill "$pid"
    rm -f "$PID_FILE"
    printf 'Semantic server stopped.\n'
    ;;
  status)
    if running_pid; then
      printf 'Semantic server running with PID %s\n' "$(<"$PID_FILE")"
      curl -fsS "http://$HOST:$PORT/models"
      printf '\n'
    else
      printf 'Semantic server is not running.\n'
      exit 1
    fi
    ;;
  *)
    printf 'Usage: %s [run|start|stop|status]\n' "$0" >&2
    exit 2
    ;;
esac
