#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
config="${KIP_UPSTREAM_CONFIG:-$PROJECT_ROOT/config/kip.example.toml}"
environment_file="${KIP_UPSTREAM_ENV_FILE:-$PROJECT_ROOT/.env.example}"
pyproject="${KIP_UPSTREAM_PYPROJECT:-$PROJECT_ROOT/pyproject.toml}"
curl_bin="${KIP_UPSTREAM_CURL:-curl}"

current_kordoc="$({
  awk '
    $0 == "[parsers.ocr.kordoc]" { in_section = 1; next }
    /^\[/ { in_section = 0 }
    in_section && $1 == "expected_version" && $2 == "=" {
      value = $3
      gsub(/^"|"$/, "", value)
      print value
      exit
    }
  ' "$config"
})"
if [[ -z "$current_kordoc" ]]; then
  printf 'missing parsers.ocr.kordoc.expected_version in %s\n' "$config" >&2
  exit 65
fi

latest_kordoc="$($curl_bin --fail --silent --show-error --retry 3 \
  https://registry.npmjs.org/kordoc/latest | jq -er '.version | strings | select(length > 0)')"
if [[ "$current_kordoc" != "$latest_kordoc" ]]; then
  printf -- "- \`kordoc\`: \`%s\` -> \`%s\`\n" "$current_kordoc" "$latest_kordoc"
fi

current_pdf_inspector="$({
  awk '
    /pdf-inspector==/ {
      value = $0
      sub(/^.*pdf-inspector==/, "", value)
      sub(/".*$/, "", value)
      print value
      exit
    }
  ' "$pyproject"
})"
if [[ -z "$current_pdf_inspector" ]]; then
  printf 'missing pdf-inspector pin in %s\n' "$pyproject" >&2
  exit 65
fi
latest_pdf_inspector="$($curl_bin --fail --silent --show-error --retry 3 \
  https://pypi.org/pypi/pdf-inspector/json | jq -er '.info.version | strings | select(length > 0)')"
if [[ "$current_pdf_inspector" != "$latest_pdf_inspector" ]]; then
  printf -- "- \`pdf-inspector\`: \`%s\` -> \`%s\`\n" \
    "$current_pdf_inspector" "$latest_pdf_inspector"
fi

while IFS='|' read -r label repository key; do
  pinned="$(awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1); exit }' "$environment_file")"
  if [[ -z "$pinned" ]]; then
    printf 'missing %s in %s\n' "$key" "$environment_file" >&2
    exit 65
  fi
  latest="$($curl_bin --fail --silent --show-error --retry 3 \
    "https://huggingface.co/api/models/$repository" | jq -er '.sha | strings | select(length > 0)')"
  if [[ "$pinned" != "$latest" ]]; then
    printf -- "- \`%s\`: \`%s\` -> \`%s\`\n" "$label" "$pinned" "$latest"
  fi
done <<'EOF'
Qwen/Qwen3-Embedding-0.6B|Qwen/Qwen3-Embedding-0.6B|KIP_EMBEDDING_REVISION
BAAI/bge-reranker-v2-m3|BAAI/bge-reranker-v2-m3|KIP_RERANKER_REVISION
EOF
