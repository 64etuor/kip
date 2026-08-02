#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 1 ]]; then
  echo "usage: $0 BACKUP_DIR" >&2
  exit 2
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
backup_dir="$1"
: "${KIP_DATABASE_URL:?set KIP_DATABASE_URL}"
pg_restore --clean --if-exists --no-owner --dbname "$KIP_DATABASE_URL" "$backup_dir/kip.dump"
"$SCRIPT_DIR/kip" migrate
"$SCRIPT_DIR/kip" rebuild --projection lexical
"$SCRIPT_DIR/kip" status
