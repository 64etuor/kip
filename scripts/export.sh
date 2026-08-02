#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
output="${1:-$PROJECT_ROOT/exports/canonical.jsonl}"
exec "$SCRIPT_DIR/kip" export canonical --output "$output"
