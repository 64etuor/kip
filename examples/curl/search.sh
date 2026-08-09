#!/usr/bin/env bash
set -euo pipefail
curl --fail --silent --show-error "${KIP_API_URL:-http://127.0.0.1:8080}/v1/search" \
  -H 'Content-Type: application/json' \
  -H "X-KIP-API-Key: ${KIP_API_KEY:?}" \
  -d '{"query":"협약 변경 승인","limit":5}' | python3 -m json.tool
