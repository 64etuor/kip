#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

BASE_URL="${KIP_SEMANTIC_BASE_URL:-http://127.0.0.1:7997}"
EMBED_SERVED="${KIP_EMBEDDING_SERVED_MODEL:-kip-qwen3-embedding-0.6b}"
RERANK_SERVED="${KIP_RERANKER_SERVED_MODEL:-kip-bge-reranker-v2-m3}"
EMBED_DIMENSIONS="${KIP_EMBEDDING_DIMENSIONS:-1024}"

"$(python_cmd)" - "$BASE_URL" "$EMBED_SERVED" "$RERANK_SERVED" "$EMBED_DIMENSIONS" <<'PY'
import sys

import httpx

base_url, embedding_model, reranker_model, raw_dimensions = sys.argv[1:]
expected_dimensions = int(raw_dimensions)
with httpx.Client(timeout=120) as client:
    models = client.get(f"{base_url}/models")
    models.raise_for_status()
    embedding = client.post(
        f"{base_url}/embeddings",
        json={
            "model": embedding_model,
            "input": ["Retrieve relevant Korean evidence: 참여율 변경 승인"],
        },
    )
    embedding.raise_for_status()
    vector = embedding.json()["data"][0]["embedding"]
    if len(vector) != expected_dimensions:
        raise SystemExit(
            f"expected {expected_dimensions} embedding dimensions, got {len(vector)}"
        )
    rerank = client.post(
        f"{base_url}/rerank",
        json={
            "model": reranker_model,
            "query": "참여율 변경 승인 근거",
            "documents": ["관련 없는 날씨 안내", "참여율 변경을 승인한다."],
            "return_documents": False,
        },
    )
    rerank.raise_for_status()
    results = rerank.json()["results"]
    if not results or results[0]["index"] != 1:
        raise SystemExit("reranker did not rank the relevant Korean evidence first")
print(f"semantic smoke passed: embedding={expected_dimensions}, reranker_top=1")
PY
