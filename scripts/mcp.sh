#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
kip_database_port_check || exit $?
PY="$(python_cmd)"
exec "$PY" -m kip.mcp_server
