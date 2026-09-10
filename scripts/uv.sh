#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
if ! command -v uv >/dev/null 2>&1; then
  printf 'uv is not ready. Run ./scripts/bootstrap.sh first.\n' >&2
  exit 69
fi
exec uv "$@"
