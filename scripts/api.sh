#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
exec "$SCRIPT_DIR/kip" api serve --host "${KIP_API_HOST:-127.0.0.1}" --port "${KIP_API_PORT:-8080}" "$@"
