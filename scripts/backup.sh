#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
source "$SCRIPT_DIR/postgres-tools.sh"
cd "$PROJECT_ROOT"
umask 077

PY="$(python_cmd)"
KIP_CLI="${KIP_CLI:-$SCRIPT_DIR/kip}"
DATABASE_URL="$($PY "$SCRIPT_DIR/secret_value.py" KIP_DATABASE_URL)"
if [[ "$DATABASE_URL" != postgresql://* && "$DATABASE_URL" != postgres://* ]]; then
  printf '%s\n' "backup requires a PostgreSQL KIP_DATABASE_URL" >&2
  exit 2
fi

BACKUP_ROOT="${KIP_BACKUP_PATH:-$PROJECT_ROOT/var/backups}"
mkdir -p "$BACKUP_ROOT"
BACKUP_ROOT="$(cd "$BACKUP_ROOT" && pwd -P)"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FINAL="$BACKUP_ROOT/$TIMESTAMP"
PARTIAL="$BACKUP_ROOT/.partial-$TIMESTAMP-$$"
if [[ -e "$FINAL" || -e "$PARTIAL" ]]; then
  printf '%s\n' "backup output already exists" >&2
  exit 2
fi
mkdir -m 700 "$PARTIAL"
COMPLETE=0
mark_failed() {
  status=$?
  if [[ "$COMPLETE" != "1" ]]; then
    printf 'exit_status=%s\n' "$status" > "$PARTIAL/FAILED"
  fi
}
trap mark_failed EXIT

export PGOPTIONS="${PGOPTIONS:+$PGOPTIONS }-c row_security=off"
postgres_dump \
  "$DATABASE_URL" \
  "$PARTIAL/kip.dump" \
  --no-owner \
  --no-privileges
postgres_query_file \
  "$DATABASE_URL" \
  "$PROJECT_ROOT/deploy/sql/backup-manifest.sql" \
  "$PARTIAL/database-manifest.json"
(
  unset KIP_DATABASE_URL_FILE
  export KIP_DATABASE_URL="$DATABASE_URL"
  export KIP_SKIP_DOTENV=1
  "$KIP_CLI" export canonical --output "$PARTIAL/canonical.jsonl"
) > "$PARTIAL/canonical-export-receipt.json"
"$PY" "$SCRIPT_DIR/backup_artifacts.py" snapshot-cas \
  --source "${KIP_CAS_PATH:-$PROJECT_ROOT/var/cas}" \
  --archive "$PARTIAL/cas.tar.gz" \
  --manifest "$PARTIAL/cas-manifest.json"
"$PY" "$SCRIPT_DIR/backup_artifacts.py" snapshot-config \
  --root "$PROJECT_ROOT" \
  --archive "$PARTIAL/configuration.tar.gz"
"$PY" "$SCRIPT_DIR/backup_artifacts.py" seal "$PARTIAL"
mv "$PARTIAL" "$FINAL"
COMPLETE=1
trap - EXIT
printf '%s\n' "$FINAL"
