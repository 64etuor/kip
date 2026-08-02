#!/usr/bin/env bash
set -euo pipefail
base="${KIP_API_URL:-http://127.0.0.1:8080}"
headers=(-H "X-KIP-Workspace: ${KIP_WORKSPACE:-default}" -H "X-KIP-Principal: curl-smoke")
if [[ -n "${KIP_API_KEY:-}" ]]; then headers+=(-H "X-KIP-API-Key: $KIP_API_KEY"); fi
curl --fail --silent --show-error "${headers[@]}" "$base/v1/capabilities" | python3 -m json.tool
