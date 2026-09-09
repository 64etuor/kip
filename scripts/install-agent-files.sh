#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

exec "$(python_cmd)" "$SCRIPT_DIR/install_agent_files.py" "$@"
