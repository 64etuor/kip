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
default_device() {
  # Apple Silicon uses Metal; a visible NVIDIA GPU uses CUDA; everything else CPU.
  if [[ "$(uname -s)/$(uname -m)" == Darwin/arm64 ]]; then printf 'mps\n'
  elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then printf 'cuda\n'
  else printf 'cpu\n'; fi
}
DEVICE="${KIP_SEMANTIC_DEVICE:-$(default_device)}"
# Half precision halves weight memory and roughly doubles GPU throughput;
# CPU inference stays float32.
if [[ "$DEVICE" == cpu ]]; then default_dtype=float32; else default_dtype=float16; fi
DTYPE="${KIP_SEMANTIC_DTYPE:-$default_dtype}"
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
if [[ "$HOST" == *:* ]]; then URL_HOST="[$HOST]"; else URL_HOST="$HOST"; fi
BASE_URL="http://$URL_HOST:$PORT"
if [[ ! -x "$SEMANTIC_VENV/bin/infinity_emb" ]]; then
  printf 'Run ./scripts/bootstrap-semantic.sh first.\n' >&2
  exit 1
fi

mkdir -p "$MODEL_CACHE" "$RUN_DIR" "$LOG_DIR"
export HF_HOME="$MODEL_CACHE"
# One cache for prefetch, the offline check and the runtime: the hub cache
# under HF_HOME. A separate SENTENCE_TRANSFORMERS_HOME (or an inherited
# transformers/hub cache variable) made the runtime look for the models
# outside the prefetched snapshots, so a fresh install could not start offline.
unset SENTENCE_TRANSFORMERS_HOME TRANSFORMERS_CACHE PYTORCH_TRANSFORMERS_CACHE \
  PYTORCH_PRETRAINED_BERT_CACHE HUGGINGFACE_HUB_CACHE
export HF_HUB_CACHE="$MODEL_CACHE/hub"
export DO_NOT_TRACK=1
export HF_HUB_DISABLE_TELEMETRY=1

# The default search mode (hybrid, ADR-065) needs only the embedding model.
# The BGE cross-encoder is loaded when `models.reranker.backend = "http"` is
# chosen: set KIP_SEMANTIC_RERANKER=on (about 2 GB more memory, and slower).
RERANKER="${KIP_SEMANTIC_RERANKER:-off}"
model_pairs=("$EMBED_MODEL" "$EMBED_REVISION")
[[ "$RERANKER" != on ]] || model_pairs+=("$RERANK_MODEL" "$RERANK_REVISION")

# Download the exact pinned revisions into the model cache. Returns non-zero
# when the network or disk refuses; the caller decides whether that is fatal.
prefetch_models() {
  "$SEMANTIC_VENV/bin/python" - "${model_pairs[@]}" <<'PY'
import sys

from huggingface_hub import snapshot_download

pairs = list(zip(sys.argv[1::2], sys.argv[2::2], strict=True))
for repository, revision in pairs:
    path = snapshot_download(repository, revision=revision)
    print(f"model ready: {repository}@{revision[:12]} -> {path}")
PY
}

models_cached() {
  HF_HUB_OFFLINE=1 "$SEMANTIC_VENV/bin/python" - "${model_pairs[@]}" >/dev/null 2>&1 <<'PY'
import sys

from huggingface_hub import snapshot_download

for repository, revision in zip(sys.argv[1::2], sys.argv[2::2], strict=True):
    snapshot_download(repository, revision=revision, local_files_only=True)
PY
}

# PyTorch's Metal allocator keeps freed buffers cached; long inputs grew the
# runtime past 12 GB on a 24 GB Mac and pushed the host into swap. Cap it
# below the device working set and release cached blocks early. A request
# that would exceed the cap fails and search falls back to lexical instead.
if [[ "$DEVICE" == mps ]]; then
  export PYTORCH_MPS_HIGH_WATERMARK_RATIO="${PYTORCH_MPS_HIGH_WATERMARK_RATIO:-0.5}"
  export PYTORCH_MPS_LOW_WATERMARK_RATIO="${PYTORCH_MPS_LOW_WATERMARK_RATIO:-0.3}"
fi

# Once the pinned snapshots are cached the server never needs the network.
# Only the actions that launch the runtime pay for this Python check.
use_cached_models_offline() {
  if models_cached; then
    export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
  fi
}

command_line=(
  "$SEMANTIC_VENV/bin/infinity_emb" v2
  --host "$HOST"
  --port "$PORT"
  --engine torch
  --device "$DEVICE"
  --no-bettertransformer
  --model-id "$EMBED_MODEL"
  --revision "$EMBED_REVISION"
  --served-model-name "$EMBED_SERVED"
  --batch-size "$EMBED_BATCH_SIZE"
  --dtype "$DTYPE"
)
if [[ "$RERANKER" == on ]]; then
  # Infinity pairs repeated per-model options by position.
  command_line+=(
    --model-id "$RERANK_MODEL"
    --revision "$RERANK_REVISION"
    --served-model-name "$RERANK_SERVED"
    --batch-size "$RERANK_BATCH_SIZE"
    --dtype "$DTYPE"
  )
fi

supervised_pid() {
  # launchd/systemd run the server in the foreground (`run`), without a PID
  # file. Matches the runtime process itself, never a `run` shell still
  # waiting for the port, so a waiting supervisor is not taken for a runtime.
  local pid
  pid="$(pgrep -f "$SEMANTIC_VENV/bin/infinity_emb v2" 2>/dev/null | head -n 1)" || true
  [[ -n "$pid" ]] || return 1
  printf '%s\n' "$pid"
}

is_runtime_pid() {
  ps -p "$1" -o command= 2>/dev/null | grep -F "$SEMANTIC_VENV" >/dev/null
}

running_pid() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi
  local pid
  pid="$(<"$PID_FILE")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" >/dev/null 2>&1 || return 1
  is_runtime_pid "$pid"
}

answers() {
  curl -fsS --max-time 5 "$BASE_URL/models" >/dev/null 2>&1
}

port_open() {
  # Something listens but may not answer HTTP yet: another checkout's runtime
  # or the compose models container while it loads (docker-proxy holds it).
  (exec 3<>"/dev/tcp/$HOST/$PORT") 2>/dev/null
}

require_reranker_served() {
  # A running embedding-only instance cannot serve the cross-encoder; a second
  # runtime could not bind the port. Unknown while it is still loading.
  [[ "$RERANKER" == on ]] || return 0
  local served
  served="$(curl -fsS --max-time 5 "$BASE_URL/models" 2>/dev/null)" || return 0
  [[ "$served" != *"\"$RERANK_SERVED\""* ]] || return 0
  printf 'The running semantic server does not serve the reranker (%s). Stop it (./scripts/semantic-server.sh stop, or its launchd/systemd service or the compose models service) and start it again with KIP_SEMANTIC_RERANKER=on.\n' "$RERANK_SERVED" >&2
  exit 1
}

supervisor_log() {
  if [[ "$(uname -s)" == Darwin ]]; then
    printf '%s\n' "$LOG_DIR/launchd-semantic.err.log"
  else
    printf 'journalctl --user -u kip-semantic\n'
  fi
}

action="${1:-run}"
case "$action" in
  run)
    # launchd KeepAlive and systemd Restart relaunch `run` whenever it exits.
    # While another runtime runs (possibly still loading) or answers on the
    # port, loading the models again would only fail to bind, so wait and
    # take over once the port is free.
    waiting=0
    while other="$(supervised_pid)" || answers || port_open; do
      if (( ! waiting )); then
        printf '%s semantic-server: another model runtime%s already runs or answers on %s; not loading a second copy. Re-checking every %ss and starting once it stops.\n' \
          "$(date '+%Y-%m-%dT%H:%M:%S')" "${other:+ (PID $other)}" "$BASE_URL" "${KIP_SEMANTIC_RECHECK_SECONDS:-60}" >&2
        waiting=1
      fi
      sleep "${KIP_SEMANTIC_RECHECK_SECONDS:-60}"
    done
    if (( waiting )); then
      printf '%s semantic-server: %s is free; starting the model runtime.\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$BASE_URL" >&2
    fi
    use_cached_models_offline
    exec "${command_line[@]}"
    ;;
  start)
    if running_pid; then
      require_reranker_served
      printf 'Semantic server already running with PID %s\n' "$(<"$PID_FILE")"
      exit 0
    fi
    if other="$(supervised_pid)" || answers; then
      # launchd/systemd (`run`) or another start already runs or serves it;
      # a second runtime would load the models again and then fail to bind.
      require_reranker_served
      printf 'Semantic server already running%s\n' "${other:+ with PID $other}"
      exit 0
    fi
    if port_open; then
      printf 'Port %s is already in use (another model runtime still loading?); not starting a second runtime.\n' "$PORT"
      exit 0
    fi
    use_cached_models_offline
    nohup "${command_line[@]}" >>"$LOG_FILE" 2>&1 &
    pid="$!"
    printf '%s\n' "$pid" >"$PID_FILE"
    printf 'Semantic server starting with PID %s; log: %s\n' "$pid" "$LOG_FILE"
    ;;
  stop)
    if ! running_pid; then
      if other="$(supervised_pid)"; then
        # launchd KeepAlive/systemd Restart would relaunch a killed instance.
        printf 'Semantic server PID %s was not started by this script; stop its supervisor: launchctl bootout gui/%s/com.kip.semantic (macOS) or systemctl --user stop kip-semantic (Linux).\n' \
          "$other" "$(id -u)"
      else
        printf 'Semantic server is not running.\n'
      fi
      exit 0
    fi
    pid="$(<"$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    # uvicorn finishes in-flight requests first; a long embedding batch can
    # keep it alive, so wait and then force it rather than orphan a process
    # that still holds several GB of model memory.
    for _ in $(seq 1 "${KIP_SEMANTIC_STOP_SECONDS:-20}"); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    # Re-check ownership: the PID may have been reused after the runtime exited.
    if kill -0 "$pid" 2>/dev/null && is_runtime_pid "$pid"; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    printf 'Semantic server stopped.\n'
    ;;
  prefetch)
    prefetch_models
    ;;
  wait)
    # Block until the server answers (first start loads the models). A
    # supervised instance (launchd/systemd `run`) has no PID file but counts.
    if running_pid || ! supervised_pid >/dev/null; then
      log_hint="$LOG_FILE"
    else
      log_hint="$(supervisor_log)"
    fi
    deadline=$(( SECONDS + ${KIP_SEMANTIC_WAIT_SECONDS:-600} ))
    while (( SECONDS < deadline )); do
      if answers; then
        require_reranker_served
        printf 'Semantic server ready on %s:%s\n' "$HOST" "$PORT"
        exit 0
      fi
      if ! running_pid && ! supervised_pid >/dev/null; then
        port_open || {
          printf 'Semantic server exited; see %s\n' "$log_hint" >&2
          exit 1
        }
        # Only a listener this checkout did not start keeps the wait going.
        log_hint="the process holding port $PORT (docker compose ps models, or lsof -iTCP:$PORT -sTCP:LISTEN)"
      fi
      sleep 1
    done
    printf 'Semantic server did not become ready; see %s\n' "$log_hint" >&2
    exit 1
    ;;
  status)
    # Exits 0 while this checkout's runtime process runs, even while it loads.
    if running_pid; then
      pid="$(<"$PID_FILE")"
    else
      pid="$(supervised_pid)" || pid=""
    fi
    if [[ -n "$pid" ]]; then
      printf 'Semantic server running with PID %s\n' "$pid"
      curl -fsS --max-time 5 "$BASE_URL/models" 2>/dev/null || printf 'not answering yet (still loading the models?)'
      printf '\n'
    else
      printf 'Semantic server is not running.\n'
      if answers; then
        printf 'Another model runtime answers on %s (for example the compose models service).\n' "$BASE_URL"
      fi
      exit 1
    fi
    ;;
  *)
    printf 'Usage: %s [run|start|stop|status|wait|prefetch]\n' "$0" >&2
    exit 2
    ;;
esac
