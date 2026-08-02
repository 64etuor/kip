# Local-first RAG Quality Stack Design

**Date:** 2026-07-30  
**Status:** Approved  
**Scope:** PostgreSQL lexical search through pgvector hybrid retrieval and reranking. Neo4j is explicitly excluded.

## Outcome

KIP will gain a reproducible retrieval-quality laboratory and a production-shaped local semantic stack without making semantic search a new canonical dependency. A third party receiving this repository will be able to:

1. install the core and optional local-model dependencies;
2. ingest an explicitly allowlisted corpus without mutating source files;
3. create and validate a versioned golden query set;
4. run lexical, vector, hybrid, and reranked retrieval variants;
5. receive machine-readable JSON plus a human-readable Markdown scorecard;
6. compare runs and promote a configuration only when the documented quality and security gates pass.

## Constraints

- PostgreSQL remains canonical. Embeddings and reports are disposable or reproducible projections.
- ACL filtering happens in SQL before vector candidates leave PostgreSQL.
- Semantic retrieval remains switchable and the lexical-only path must continue to work.
- Initial real-corpus evaluation uses only public Korean government documents.
- OneDrive and company Google Drive are excluded until a source directory is explicitly allowlisted.
- Model output never creates an approved assertion.
- No Neo4j service, schema, dependency, or runtime path is added.
- Source text, PII, and secrets are not copied into the evolution ledger.

## Selected approach

Use a local-first, provider-pluggable architecture with a persistent inference sidecar.

- **Embedding candidate A:** `Qwen/Qwen3-Embedding-0.6B`, 1024 dimensions, multilingual, instruction-aware.
- **Embedding candidate B:** `BAAI/bge-m3`, 1024 dimensions, multilingual.
- **Reranker candidate:** `BAAI/bge-reranker-v2-m3`.
- **Local serving:** Infinity on `127.0.0.1`, using Apple MPS when supported and CPU fallback when a model/backend combination is not stable.
- **Application integration:** small HTTP adapters behind KIP ports. The domain and application layers do not import PyTorch, Transformers, Infinity, or vendor SDKs.
- **Persistence:** a new append-only migration creates a 1024-dimensional pgvector projection and RLS policy.

The named models are candidates, not assumptions. The checked-in evaluation harness decides which configuration is active on this corpus. Qwen3's model card reports 100+ languages, up to 1024 dimensions, a 32K context window, and Apache-2.0 licensing. BGE-M3 and the BGE v2 M3 reranker provide a mature multilingual comparison under the FlagEmbedding MIT-licensed toolkit.

## Why the sidecar approach

### Selected: persistent local sidecar

The CLI currently starts a new Python process for each command. Loading one or two transformer models inside every CLI process would dominate latency and duplicate memory. A persistent sidecar pays model startup once, supports both embedding and reranking endpoints, can be health-checked independently, and keeps KIP's core dependency set light.

### Rejected: in-process models in every KIP process

This is simpler in a notebook but creates poor CLI cold-start behavior, makes API workers duplicate model memory, and couples the core package to PyTorch and model-specific code.

### Rejected as default: managed embedding API

It is operationally easy but introduces data egress, recurring cost, secret management, and provider drift. A future remote adapter may implement the same port, but remote egress remains disabled by default.

## Retrieval flow

```text
query
  -> Korean analyzer
  -> lexical top-N (exact identifiers + substring + FTS + trigram)
  -> query embedding
  -> ACL-prefiltered pgvector exact top-N
  -> reciprocal-rank fusion
  -> optional rerank of the fused top-N
  -> stable SearchHit contract with locator and source hash
```

Key choices:

- Candidate generation defaults to 40 lexical plus 40 vector units.
- Reciprocal-rank fusion defaults to `k=60`; raw lexical and cosine scores are never compared directly.
- Exact identifier hits receive a protected lexical rank and cannot be displaced solely by a semantic score.
- Reranking is limited to the fused top 6 by default. The initial depth of 20
  preserved quality but exceeded the two-second P95 gate on the pilot M4 Pro;
  depth 6 preserved every measured rank while reducing P95 to 1.85 seconds.
- The final API/CLI limit is applied after fusion and reranking.
- HNSW is not created initially. Exact pgvector search is measured first; HNSW is an evidence-gated optimization for a larger corpus.
- Search-hit metadata records retrieval channels and ranks without changing the stable public fields.

## Embedding-space lifecycle

An embedding space records:

- provider and served model name;
- immutable model revision when available;
- dimensions and normalization;
- document/query instructions;
- distance metric;
- configuration hash;
- `inactive`, `shadow`, or `active` status.

Rebuilding a space:

1. reads only current active content units;
2. embeds title plus normalized body in deterministic batches;
3. stores the source-content hash with each vector;
4. upserts the selected space without deleting other spaces;
5. verifies unit/vector parity;
6. leaves activation to an explicit quality-gate decision.

## Evaluation contract

Golden cases are YAML and contain no generated expected answer. Each case names expected and forbidden stable document IDs or source-relative paths, expected locator constraints, category, principal/scopes, and `k`.

Required categories for the initial public pilot:

- exact identifier/title;
- Korean professional terminology;
- semantic paraphrase;
- table or spreadsheet text where available;
- latest-version selection;
- access-denied;
- stale-source warning.

Every run computes:

- document Recall@K;
- MRR;
- nDCG@K;
- zero-result rate and recovery rate;
- evidence locator accuracy;
- latest-version accuracy;
- unauthorized result count;
- query latency p50, p95, and maximum;
- per-category metrics;
- corpus, golden-set, configuration, and model fingerprints.

The evaluator runs each requested retrieval variant against the same frozen case list. It emits:

- `evaluation/reports/<run-id>.json`: complete machine-readable evidence;
- `evaluation/reports/<run-id>.md`: periodic scorecard for humans;
- `evaluation/reports/evolution.jsonl`: one append-only comparison record per run;
- `evaluation/reports/latest.json` and `latest.md`: replaceable convenience pointers.

The ledger contains hashes, metrics, decisions, and short redacted notes. It does not contain source bodies or query results that could reveal restricted content.

## Promotion and evolution rules

Semantic search remains shadow-only unless:

- overall Recall@10 improves by at least 3 percentage points, or semantic-category Recall@10 improves by at least 10 points;
- exact/identifier category regression is at most 1 point;
- unauthorized result count is zero;
- stale-source warning rate remains 100%;
- P95 search latency is at most 2 seconds on the declared pilot hardware.

The evaluator may recommend the next experiment, such as changing fusion depth, instruction text, or rerank depth. It never edits active configuration or promotes a model automatically. This produces a durable self-improvement trail while retaining human control.

## Corpus safety

The public-corpus acquisition script uses a manifest of explicit HTTPS URLs,
expected licenses, and expected SHA-256 values. Downloads go to the gitignored
`var/public-corpus/`, not to the canonical source tree. Each manifest entry
records its source page, download URL, checksum, license label, and attribution.

The script rejects redirects to non-HTTPS destinations, unexpected content types, and files whose checksum differs once a manifest checksum has been pinned. It never crawls OneDrive or a general cloud-storage root.

## Operational surface

Primary commands:

```bash
./scripts/bootstrap-semantic.sh
./scripts/semantic-server.sh run
./scripts/fetch_public_corpus.py
./scripts/kip migrate
./scripts/kip sync run --source public-government
./scripts/kip projection rebuild --name semantic
./scripts/kip projection verify --name semantic
./scripts/kip evaluate validate --dataset evaluation/golden/public-government.yaml
make evaluate
./scripts/kip evaluate compare --report evaluation/reports/public-government/latest.json --baseline lexical --candidate reranked
```

A scheduled operator can run the same evaluator through `make evaluate`. Reports are deterministic for a fixed corpus, golden set, configuration, and model revision except for measured latency.

## Failure behavior

- If the model sidecar is unavailable and semantic search is optional, search falls back to lexical and reports the degradation in metadata/capabilities.
- If semantic search is explicitly required for an evaluation variant, sidecar failure makes that variant fail; it is never silently scored as lexical.
- Vector rows with stale source hashes are excluded from semantic retrieval until rebuilt.
- A failed semantic rebuild does not change the active embedding space.
- A failed corpus download does not remove an earlier valid local copy.

## External references

- Qwen3 Embedding model card: <https://huggingface.co/Qwen/Qwen3-Embedding-0.6B>
- FlagEmbedding: <https://github.com/FlagOpen/FlagEmbedding>
- Sentence Transformers: <https://www.sbert.net/>
- Infinity: <https://github.com/michaelfeil/infinity>
- pgvector: <https://github.com/pgvector/pgvector>
- 공공데이터 이용정책: <https://www.data.go.kr/ugs/selectPortalPolicyView.do>
