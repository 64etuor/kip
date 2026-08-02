#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
curl --fail --silent --show-error "${KIP_API_URL:-http://127.0.0.1:8080}/v1/connectors/events" \
  -H 'Content-Type: application/json' \
  -H "X-KIP-API-Key: ${KIP_API_KEY:?}" \
  -H "X-KIP-Admin-Key: ${KIP_ADMIN_KEY:?}" \
  -H "X-KIP-Workspace: ${KIP_WORKSPACE:-default}" \
  -H "X-KIP-Principal: ${KIP_PRINCIPAL_ID:-connector-example}" \
  -H "X-KIP-ACL-Scopes: ${KIP_ACL_SCOPES:-workspace:default}" \
  --data-binary "@$ROOT/examples/connector/event.json" | python3 -m json.tool
