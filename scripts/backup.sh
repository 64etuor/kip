#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
cd "$PROJECT_ROOT"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
out="${KIP_BACKUP_PATH:-$PROJECT_ROOT/var/backups}/$ts"
mkdir -p "$out"
pg_dump --format=custom --file "$out/kip.dump" "${KIP_DATABASE_URL:?set KIP_DATABASE_URL}"
tar -czf "$out/config-ontology.tgz" config/kip.example.toml ontology migrations VERSION
if [[ -d "${KIP_CAS_PATH:-$PROJECT_ROOT/var/cas}" ]]; then
  find "${KIP_CAS_PATH:-$PROJECT_ROOT/var/cas}" -type f -print0 | sort -z | xargs -0 shasum -a 256 > "$out/cas-manifest.sha256" || true
fi
printf '%s\n' "$out"
