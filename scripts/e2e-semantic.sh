#!/usr/bin/env bash
# End-to-end checks for the model runtime, in two modes with very different
# costs. See docs/OPERATIONS.md for where each one runs.
#
#   --mode served-model   (default; measured 5s, no model download)
#     Reproduces the 3.12.2 defect. A stub runtime advertises a model name
#     that is NOT the configured one and answers /embeddings with HTTP 200
#     anyway, exactly as Infinity does. Asserts that KIP refuses to embed
#     against it: `kip doctor` names the mismatch, default-mode search
#     degrades with `semantic_degraded` instead of returning foreign vectors,
#     and an explicit --mode vector fails. Cheap enough to run on every push.
#
#   --mode offline-runtime  (estimated 20-30 minutes; downloads ~1.2 GB of
#                            weights. NOT yet executed - see the note below)
#     Reproduces the 3.12.1 defect. Bootstraps a deployment with semantic
#     search ON so bootstrap prefetches the pinned snapshot, then starts the
#     runtime with the Hugging Face hub forced offline and runs
#     scripts/semantic-smoke.sh against it. When prefetch and the runtime
#     disagree about the cache, the runtime cannot find the weights and this
#     fails. It downloads over a gigabyte and holds several GB of RAM, so it
#     is NOT on the push path: run it from the scheduled/manual
#     `semantic-runtime` workflow before tagging.
#
#     Honest status: the served-model mode above has been run and passes; this
#     mode has not been executed yet, because the machine it was written on
#     already serves a live model runtime that must not be disturbed. Its first
#     real run is the scheduled workflow (or a manual dispatch); treat a first
#     failure there as a defect in this script until proven otherwise.
#
#   ./scripts/e2e-semantic.sh
#   ./scripts/e2e-semantic.sh --mode offline-runtime
#
# This script never touches a model runtime that belongs to the machine: the
# offline mode installs its own runtime inside a throwaway deployment, on a
# port this run picked, with its own PID file and model cache under that
# deployment's var/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
E2E_PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=e2e-common.sh
source "$SCRIPT_DIR/e2e-common.sh"

mode=served-model
keep_work=0
work=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) [[ $# -ge 2 ]] || e2e_fail "--mode needs served-model or offline-runtime"; mode="$2"; shift 2 ;;
    --mode=*) mode="${1#--mode=}"; shift ;;
    --keep) keep_work=1; shift ;;
    --work) [[ $# -ge 2 ]] || e2e_fail "--work needs a value"; work="$2"; shift 2 ;;
    -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
    *) e2e_fail "unknown option: $1" ;;
  esac
done
[[ "$mode" == served-model || "$mode" == offline-runtime ]] \
  || e2e_fail "--mode must be served-model or offline-runtime"

E2E_PYTHON="${KIP_E2E_PYTHON:-python3}"
e2e_require_command "$E2E_PYTHON"
version="$(tr -d '[:space:]' < "$E2E_PROJECT_ROOT/VERSION")"
checker="$E2E_PROJECT_ROOT/tests/e2e/kip_envelope.py"

if [[ -z "$work" ]]; then work="$(mktemp -d "${TMPDIR:-/tmp}/kip-e2e-semantic.XXXXXX")"; fi
mkdir -p "$work"
# -P: the physical path. Scripts under test resolve symlinks (backup.sh
# uses `pwd -P`), and on macOS /tmp is a symlink, so a logical path here would
# name a directory that is not the one bind-mounted into a container.
work="$(cd "$work" && pwd -P)"
STUB_PID=""
DEPLOYMENT=""
cleanup() {
  local status=$?
  [[ -z "$STUB_PID" ]] || kill "$STUB_PID" 2>/dev/null || true
  if [[ -n "$DEPLOYMENT" && -x "$DEPLOYMENT/scripts/semantic-server.sh" ]]; then
    ( cd "$DEPLOYMENT" && ./scripts/semantic-server.sh stop >/dev/null 2>&1 ) || true
  fi
  e2e_stop_postgres
  if [[ "$keep_work" == 1 ]]; then
    printf 'e2e: work tree kept at %s\n' "$work" >&2
  else
    rm -rf "$work"
  fi
  e2e_assert_real_home_unchanged || exit 1
  return "$status"
}
trap cleanup EXIT

# ======================================================= served-model mode
run_served_model_mode() {
  local stub_port configured_model="kip-qwen3-embedding-0.6b"
  stub_port="$(e2e_free_port)"
  e2e_start_postgres

  e2e_log "Starting a stub runtime that serves a DIFFERENT model on 127.0.0.1:$stub_port"
  "$E2E_PYTHON" "$E2E_PROJECT_ROOT/tests/e2e/wrong_model_runtime.py" \
    --port "$stub_port" --served-model kip-some-other-embedding-v9 --dimensions 1024 \
    > "$work/stub.log" 2>&1 &
  STUB_PID=$!
  local attempt=0
  until curl -fsS --max-time 2 "http://127.0.0.1:$stub_port/models" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    (( attempt < 50 )) || { cat "$work/stub.log" >&2; e2e_fail "the stub runtime never answered"; }
    sleep 0.2
  done
  e2e_note "stub /models advertises kip-some-other-embedding-v9"

  # A configuration identical to the shipped profile except for the runtime
  # address: semantic search on, hybrid default, the release's served name.
  "$E2E_PYTHON" - "$E2E_PROJECT_ROOT/config/kip.example.toml" "$work/kip.toml" "$stub_port" <<'CONFIG'
import sys
from pathlib import Path

source, destination, port = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
text = source.read_text(encoding="utf-8")
text = text.replace('base_url = "http://127.0.0.1:7997"', f'base_url = "http://127.0.0.1:{port}"')
if "semantic_enabled = true" not in text:
    raise SystemExit("the example config no longer enables semantic search")
# 7997 is the port a developer machine's LIVE model runtime listens on. If the
# example config's spelling ever changes, the replace above silently does
# nothing and this check would embed against that runtime instead of the stub.
if ":7997" in text:
    raise SystemExit("the rewritten config still names port 7997; the base_url spelling in "
                     "config/kip.example.toml changed and the stub was not wired in")
if f'base_url = "http://127.0.0.1:{port}"' not in text:
    raise SystemExit("the rewritten config does not point at the stub runtime")
destination.write_text(text, encoding="utf-8")
CONFIG

  kip_run() {
    e2e_clean_env \
      KIP_SKIP_DOTENV=1 \
      KIP_CONFIG="$work/kip.toml" \
      KIP_DATABASE_URL="$E2E_DATABASE_URL" \
      KIP_CAS_PATH="$work/cas" \
      KIP_ENV=test \
      "$E2E_PROJECT_ROOT/scripts/kip" "$@"
  }

  e2e_log "Preparing a corpus so retrieval has something to rank"
  kip_run migrate > "$work/migrate.json"
  kip_run sync run --source sample > "$work/sync.json"

  e2e_log "kip doctor names the mismatch instead of trusting the runtime"
  local status=0
  kip_run doctor > "$work/doctor.json" 2> "$work/doctor.err" || status=$?
  grep -qF "not the configured" "$work/doctor.json" \
    || { cat "$work/doctor.json" >&2; e2e_fail "kip doctor did not report that the runtime serves another model"; }
  grep -qF "$configured_model" "$work/doctor.json" \
    || e2e_fail "kip doctor did not name the configured model in the mismatch"
  e2e_note "doctor reports the served/configured mismatch (exit $status)"

  e2e_log "Default-mode search degrades instead of embedding with the wrong model"
  status=0
  kip_run search "정산" --limit 5 > "$work/search.json" || status=$?
  e2e_assert_status 0 "$status" "default-mode search still answers"
  # semantic_degraded is the whole point: the release promises lexical results
  # with a warning, never results from a foreign embedding space.
  "$E2E_PYTHON" "$checker" search "$work/search.json" --min-hits 1 \
    --allow-warning semantic_degraded
  grep -qF 'semantic_degraded' "$work/search.json" \
    || e2e_fail "search did not report semantic_degraded while the runtime served another model"
  e2e_note "search reported semantic_degraded"

  e2e_log "An explicitly requested vector search fails rather than lying"
  status="$(e2e_capture "$work/vector.json" kip_run search "정산" --mode vector --limit 5)"
  [[ "$status" != "0" ]] \
    || { cat "$work/vector.json" >&2; e2e_fail "--mode vector succeeded against a runtime serving another model"; }
  e2e_note "explicit --mode vector exited $status"
  grep -qF "not the configured" "$work/vector.json.stderr" \
    || { cat "$work/vector.json.stderr" >&2; e2e_fail "the vector-mode error did not name the served/configured mismatch"; }

  e2e_log "PASS (served-model): a runtime serving another model can no longer embed queries"
}

# ===================================================== offline-runtime mode
run_offline_runtime_mode() {
  local archive mirror home semantic_port
  archive="$work/dist/kip-$version.zip"
  e2e_build_package "$archive"
  mirror="$work/releases"
  mkdir -p "$mirror/v$version"
  cp "$archive" "$archive.sha256" "$mirror/v$version/"

  home="$work/home"
  mkdir -p "$home"
  DEPLOYMENT="$work/deployment"
  semantic_port="$(e2e_free_port)"

  # Semantic search ON: bootstrap installs the runtime and prefetches the
  # pinned snapshot into this deployment's own var/model-cache.
  e2e_log "Installing and bootstrapping a deployment with semantic search on"
  e2e_clean_env HOME="$home" SHELL=/bin/bash \
    ${E2E_DOCKER_CONFIG[@]+"${E2E_DOCKER_CONFIG[@]}"} \
    KIP_RELEASE_BASE_URL="file://$mirror" \
    KIP_SEMANTIC_PORT="$semantic_port" \
    bash "$E2E_PROJECT_ROOT/scripts/install.sh" "$DEPLOYMENT" \
      --version "$version" --without-docker --no-shell-profile --bin-dir "$home/bin"

  if grep -q '^KIP_SEMANTIC=off' "$DEPLOYMENT/.env"; then
    e2e_fail "bootstrap could not install the model runtime; it recorded KIP_SEMANTIC=off"
  fi
  [[ -x "$DEPLOYMENT/var/semantic-venv/bin/infinity_emb" ]] \
    || e2e_fail "bootstrap installed no model runtime at $DEPLOYMENT/var/semantic-venv"
  e2e_note "runtime and pinned snapshot are prefetched"

  # The defect: prefetch wrote the snapshot into one cache and the runtime
  # looked in another, so a fresh install could not start without the network.
  # Forcing the hub offline makes the runtime prove it reads what was fetched.
  e2e_log "Starting the runtime with the Hugging Face hub forced offline"
  semantic() {
    ( cd "$DEPLOYMENT" && e2e_clean_env HOME="$home" \
        KIP_SEMANTIC_PORT="$semantic_port" \
        HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
        ./scripts/semantic-server.sh "$@" )
  }
  if ! semantic start || ! semantic wait; then
    tail -n 120 "$DEPLOYMENT/var/log/semantic-server.log" >&2 || true
    e2e_fail "the model runtime could not start offline from the cache bootstrap prefetched.
  This is the 3.12.1 defect: prefetch and the runtime disagreed about where the
  pinned snapshot lives, so a fresh install stayed lexical."
  fi
  e2e_note "the runtime started with no network access to the model hub"

  e2e_log "The offline runtime embeds the release's pinned model"
  ( cd "$DEPLOYMENT" && e2e_clean_env HOME="$home" \
      KIP_SEMANTIC_BASE_URL="http://127.0.0.1:$semantic_port" \
      ./scripts/semantic-smoke.sh )

  e2e_log "PASS (offline-runtime): a fresh install starts its model runtime offline"
}

if [[ "$mode" == served-model ]]; then run_served_model_mode; else run_offline_runtime_mode; fi
