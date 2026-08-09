#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf '%s\n' "usage: $0 BACKUP_DIR" >&2
  exit 2
fi
if [[ "${KIP_RESTORE_DRILL_CONFIRM:-}" != "YES" ]]; then
  printf '%s\n' "set KIP_RESTORE_DRILL_CONFIRM=YES to run the isolated restore drill" >&2
  exit 2
fi
: "${KIP_DRILL_GOLDEN_DATASET:?set KIP_DRILL_GOLDEN_DATASET}"
: "${KIP_DRILL_BASELINE_REPORT:?set KIP_DRILL_BASELINE_REPORT}"
: "${KIP_DRILL_CAS_PATH:?set KIP_DRILL_CAS_PATH to an absent or empty absolute path}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
PY="$(python_cmd)"
KIP_CLI="${KIP_CLI:-$SCRIPT_DIR/kip}"
REPORT_ROOT="${KIP_DRILL_REPORT_PATH:-$PROJECT_ROOT/var/restore-drills/$(date -u +%Y%m%dT%H%M%SZ)}"
if [[ -e "$REPORT_ROOT" ]]; then
  printf '%s\n' "restore drill report path already exists" >&2
  exit 2
fi
mkdir -m 700 -p "$REPORT_ROOT"

if [[ -n "${KIP_DRILL_DATABASE_URL:-}" ]]; then
  export KIP_RESTORE_DATABASE_URL="$KIP_DRILL_DATABASE_URL"
elif [[ -n "${KIP_DRILL_DATABASE_URL_FILE:-}" ]]; then
  export KIP_RESTORE_DATABASE_URL_FILE="$KIP_DRILL_DATABASE_URL_FILE"
else
  printf '%s\n' "set KIP_DRILL_DATABASE_URL or KIP_DRILL_DATABASE_URL_FILE" >&2
  exit 2
fi
export KIP_RESTORE_CAS_PATH="$KIP_DRILL_CAS_PATH"
export KIP_RESTORE_CONFIRM=YES
export KIP_RESTORE_EVIDENCE_PATH="$REPORT_ROOT/restore"
"$SCRIPT_DIR/restore.sh" "$1" > "$REPORT_ROOT/restore-command.txt"

TARGET_URL="$($PY "$SCRIPT_DIR/secret_value.py" KIP_RESTORE_DATABASE_URL)"
EVALUATION_ARGS=(
  --dataset "$KIP_DRILL_GOLDEN_DATASET"
  --variants "${KIP_DRILL_VARIANTS:-lexical}"
  --warmup-passes 0
  --output-dir "$REPORT_ROOT/evaluation"
)
if [[ -n "${KIP_DRILL_REVIEW_BUNDLE:-}" ]]; then
  EVALUATION_ARGS+=(--reviews "$KIP_DRILL_REVIEW_BUNDLE")
fi
(
  unset KIP_DATABASE_URL_FILE
  export KIP_DATABASE_URL="$TARGET_URL"
  export KIP_DATABASE_STATEMENT_TIMEOUT_MS="${KIP_RESTORE_STATEMENT_TIMEOUT_MS:-300000}"
  export KIP_CAS_PATH="$KIP_DRILL_CAS_PATH"
  export KIP_SKIP_DOTENV=1
  "$KIP_CLI" evaluate run "${EVALUATION_ARGS[@]}" \
    > "$REPORT_ROOT/evaluation-command.json"
)
"$PY" "$SCRIPT_DIR/backup_artifacts.py" compare-evaluation \
  --baseline "$KIP_DRILL_BASELINE_REPORT" \
  --actual "$REPORT_ROOT/evaluation/latest.json" \
  > "$REPORT_ROOT/evaluation-comparison.json"
"$PY" "$SCRIPT_DIR/backup_artifacts.py" receipt \
  --kind restore-drill \
  --evidence "$REPORT_ROOT" \
  --output "$REPORT_ROOT/receipt.json"
printf '%s\n' "$REPORT_ROOT"
