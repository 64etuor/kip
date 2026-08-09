#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
curl --fail --silent --show-error "${KIP_API_URL:-http://127.0.0.1:8080}/v1/connectors/events" \
  -H 'Content-Type: application/json' \
  -H "X-KIP-API-Key: ${KIP_API_KEY:?}" \
  -H "X-KIP-Admin-Key: ${KIP_ADMIN_KEY:?}" \
  --data-binary "@$ROOT/examples/connector/event.json" | python3 -m json.tool
