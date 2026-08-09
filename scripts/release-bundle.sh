#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT="${1:-$PROJECT_ROOT/dist/kip-$(tr -d '[:space:]' < "$PROJECT_ROOT/VERSION")}"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3 || command -v python)"
fi

: "${KIP_API_IMAGE:?set KIP_API_IMAGE to a repository@sha256 reference}"
: "${KIP_WORKER_IMAGE:?set KIP_WORKER_IMAGE to a repository@sha256 reference}"
: "${KIP_MIGRATE_IMAGE:?set KIP_MIGRATE_IMAGE to a repository@sha256 reference}"

WHEEL="${KIP_RELEASE_WHEEL:-}"
WHEEL_TMP=""
if [[ -z "$WHEEL" ]]; then
  command -v uv >/dev/null
  WHEEL_TMP="$(mktemp -d "${TMPDIR:-/tmp}/kip-release-wheel.XXXXXX")"
  trap 'rm -rf "$WHEEL_TMP"' EXIT
  uv build \
    --build-constraints "$PROJECT_ROOT/requirements/build.txt" \
    --wheel \
    --out-dir "$WHEEL_TMP" \
    "$PROJECT_ROOT"
  WHEELS=("$WHEEL_TMP"/*.whl)
  if [[ ${#WHEELS[@]} -ne 1 || ! -f "${WHEELS[0]}" ]]; then
    printf '%s\n' "release build did not produce exactly one wheel" >&2
    exit 1
  fi
  WHEEL="${WHEELS[0]}"
fi

ARGS=(
  build
  --root "$PROJECT_ROOT"
  --output "$OUTPUT"
  --wheel "$WHEEL"
  --api-image "$KIP_API_IMAGE"
  --worker-image "$KIP_WORKER_IMAGE"
  --migrate-image "$KIP_MIGRATE_IMAGE"
)
if [[ "${KIP_RELEASE_ALLOW_DIRTY:-0}" == "1" ]]; then
  ARGS+=(--allow-dirty)
fi
"$PYTHON" "$SCRIPT_DIR/release_artifacts.py" "${ARGS[@]}"
