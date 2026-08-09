#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf '%s\n' "usage: $0 BACKUP_DIR" >&2
  exit 2
fi
if [[ "${KIP_RESTORE_CONFIRM:-}" != "YES" ]]; then
  printf '%s\n' "set KIP_RESTORE_CONFIRM=YES for an isolated empty restore target" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
source "$SCRIPT_DIR/postgres-tools.sh"
cd "$PROJECT_ROOT"
umask 077

PY="$(python_cmd)"
KIP_CLI="${KIP_CLI:-$SCRIPT_DIR/kip}"
BACKUP_DIR="$(cd "$1" && pwd -P)"
TARGET_URL="$($PY "$SCRIPT_DIR/secret_value.py" KIP_RESTORE_DATABASE_URL)"
TARGET_CAS="${KIP_RESTORE_CAS_PATH:?set KIP_RESTORE_CAS_PATH to an isolated CAS path}"
case "$TARGET_CAS" in
  /*) ;;
  *)
    printf '%s\n' "KIP_RESTORE_CAS_PATH must be absolute" >&2
    exit 2
    ;;
esac
if [[ -n "${KIP_DATABASE_URL:-}" && "$TARGET_URL" == "$KIP_DATABASE_URL" ]]; then
  printf '%s\n' "restore target must differ from KIP_DATABASE_URL" >&2
  exit 2
fi

"$PY" "$SCRIPT_DIR/backup_artifacts.py" verify "$BACKUP_DIR"
TARGET_RELATIONS="$(postgres_query "$TARGET_URL" "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_toast';")"
if [[ "$TARGET_RELATIONS" != "0" ]]; then
  printf '%s\n' "restore database must contain no user tables" >&2
  exit 2
fi

EVIDENCE_ROOT="${KIP_RESTORE_EVIDENCE_PATH:-$PROJECT_ROOT/var/restore-evidence/$(date -u +%Y%m%dT%H%M%SZ)}"
if [[ -e "$EVIDENCE_ROOT" ]]; then
  printf '%s\n' "restore evidence path already exists" >&2
  exit 2
fi
mkdir -m 700 -p "$EVIDENCE_ROOT"
export PGOPTIONS="${PGOPTIONS:+$PGOPTIONS }-c row_security=off"
postgres_restore \
  "$TARGET_URL" \
  "$BACKUP_DIR/kip.dump" \
  --exit-on-error \
  --single-transaction \
  --no-owner \
  --no-privileges
postgres_query_file \
  "$TARGET_URL" \
  "$PROJECT_ROOT/deploy/sql/backup-manifest.sql" \
  "$EVIDENCE_ROOT/restored-database-manifest.json"
"$PY" "$SCRIPT_DIR/backup_artifacts.py" compare-database \
  --expected "$BACKUP_DIR/database-manifest.json" \
  --actual "$EVIDENCE_ROOT/restored-database-manifest.json" \
  > "$EVIDENCE_ROOT/database-comparison.json"
"$PY" "$SCRIPT_DIR/backup_artifacts.py" restore-cas \
  --archive "$BACKUP_DIR/cas.tar.gz" \
  --manifest "$BACKUP_DIR/cas-manifest.json" \
  --target "$TARGET_CAS" \
  > "$EVIDENCE_ROOT/cas-verification.json"
(
  unset KIP_DATABASE_URL_FILE
  export KIP_DATABASE_URL="$TARGET_URL"
  export KIP_DATABASE_STATEMENT_TIMEOUT_MS="${KIP_RESTORE_STATEMENT_TIMEOUT_MS:-300000}"
  export KIP_CAS_PATH="$TARGET_CAS"
  export KIP_SKIP_DOTENV=1
  "$KIP_CLI" migrate > "$EVIDENCE_ROOT/migrate.json"
  "$KIP_CLI" projection rebuild --name lexical > "$EVIDENCE_ROOT/rebuild-lexical.json"
  postgres_query "$TARGET_URL" "ANALYZE;" > "$EVIDENCE_ROOT/analyze.txt"
  "$KIP_CLI" projection verify --name lexical > "$EVIDENCE_ROOT/verify-lexical.json"
  "$KIP_CLI" projection verify --name graph > "$EVIDENCE_ROOT/verify-graph.json"
  "$KIP_CLI" status > "$EVIDENCE_ROOT/status.json"
)
"$PY" "$SCRIPT_DIR/backup_artifacts.py" receipt \
  --kind restore \
  --evidence "$EVIDENCE_ROOT" \
  --output "$EVIDENCE_ROOT/receipt.json"
printf '%s\n' "$EVIDENCE_ROOT"
