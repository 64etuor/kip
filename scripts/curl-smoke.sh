#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
PY="$(python_cmd)"
base="${KIP_API_URL:-http://127.0.0.1:${KIP_API_PORT:-8080}}"
base="${base%/}"
api_key="$("$PY" -c 'from kip.settings import Settings; print(Settings.load().api_key, end="")')"
headers=(-H "Accept: application/json")
if [[ -n "$api_key" ]]; then headers+=(-H "X-KIP-API-Key: $api_key"); fi
for endpoint in readyz v1/capabilities; do
  curl --fail --silent --show-error "${headers[@]}" "$base/$endpoint" | "$PY" -m json.tool
done
