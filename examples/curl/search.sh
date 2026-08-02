#!/usr/bin/env bash
set -euo pipefail
curl --fail --silent --show-error "${KIP_API_URL:-http://127.0.0.1:8080}/v1/search" \
  -H 'Content-Type: application/json' \
  -H "X-KIP-API-Key: ${KIP_API_KEY:?}" \
  -H "X-KIP-Workspace: ${KIP_WORKSPACE:-default}" \
  -H "X-KIP-Principal: ${KIP_PRINCIPAL_ID:-example-app}" \
  -H "X-KIP-ACL-Scopes: ${KIP_ACL_SCOPES:-workspace:default}" \
  -d '{"query":"협약 변경 승인","limit":5}' | python3 -m json.tool
