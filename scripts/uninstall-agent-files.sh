#!/usr/bin/env bash
# Remove KIP skill copies installed from this deployment:
#   ./scripts/uninstall-agent-files.sh [personal | project [DIR]] [--client claude|codex|all]
# Only skill directories carrying this deployment's install record are removed.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

exec "$(python_cmd)" "$SCRIPT_DIR/install_agent_files.py" --uninstall "$@"
