# RAG evaluation evidence

## Current decision

Keep semantic search disabled by default. The reason is no longer “semantic
quality did not improve” for every corpus. On the reviewed 19-case private
OneDrive set, vector-only retrieval materially beats the active lexical path,
and its HNSW P95 passes, but its stale-warning gate fails closed. On the six-document public
pilot, lexical remains equal or better. These are separate corpus decisions.

Public v1 `SearchRequest.mode` exposes `lexical`, `vector`, `hybrid`, and
`reranked` consistently across the four edges. `kip evaluate run` remains the
fingerprinted comparison surface; a one-off explicit mode request is diagnostic
evidence and never substitutes for projection promotion and activation.

The latest loaded-corpus audit, including the corrected unmeasured-metric
semantics and all four retrieval variants, is
[`RAG_QUALITY_AUDIT_2026-08-06.md`](RAG_QUALITY_AUDIT_2026-08-06.md).

## Reviewed private shadow: 2026-08-13

The current Qwen3 space
`qwen3-embedding-0.6b-1024-c4000-ht1` is complete at 30,565/30,565 current
active ACL-fresh units. It uses the pinned local Infinity sidecar and versioned
`head_tail_v1` input policy from ADR-035.

| Variant | Recall@10 | MRR | nDCG@10 | P95 ms | ACL leaks |
|---|---:|---:|---:|---:|---:|
| lexical | 0.789 | 0.646 | 0.680 | 7385.80 | 0 |
| vector HNSW | **0.947** | **0.822** | **0.853** | **133.75** | 0 |
| hybrid | 0.895 | 0.702 | 0.750 | 7924.98 | 0 |
| reranked | 0.842 | 0.656 | 0.700 | 15512.58 | 0 |

Semantic-paraphrase Recall@10 improves from `0.429` to `0.857`; exact
identifier and exact numeric Recall@10 remain `1.000`. Nevertheless the
decision is `keep_disabled`: vector P95 passes the `2000 ms` ceiling, but the
retrieval-only dataset contains no stale-warning observations, so that mandatory
gate is `null`. No activation command ran. The authoritative report is
`evaluation/reports/semantic-qwen3-all-modes-final-20260813/decision.md`.

## Reproducible public pilot

- Hardware: Apple Silicon M4 Pro, arm64, 24 GB unified memory
- Runtime: CPython 3.13.13
- Corpus: six Korean government PDFs, Public Nuri Type 1
- Corpus fingerprint: `sha256:68d47873a9c605021581b1522b6b6e0fd1e486cb6ee0cf82b4633e35a76fb6f8`
- Canonical/search units: 74
- Golden set: 30 reviewed relevance questions plus six ACL-denial cases
- Embedding: Qwen3 Embedding 0.6B, revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, 1024 dimensions
- Reranker: BGE reranker v2 M3, revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`
- Vector backend: PostgreSQL 18, pgvector 0.8.2, exact cosine search
- Benchmark protocol: one untimed full-corpus warmup pass per variant
- Current code fingerprint: `sha256:b31989422bd2693026d91f7d7862f0852accd3e204e945d513f01bf90f0e8e62`

## Evolution record

| Run | Change | Variant | Recall@10 | MRR | nDCG@10 | P95 ms | ACL leaks |
|---|---|---|---:|---:|---:|---:|---:|
| `eval_20260730T105915336204Z` | Initial natural-query baseline | lexical | 0.167 | 0.167 | 0.167 | 90.11 | 0 |
| `eval_20260730T105915336204Z` | Initial semantic shadow | vector | 1.000 | 0.972 | 0.979 | 323.98 | 0 |
| `eval_20260730T105915336204Z` | Initial RRF | hybrid | 1.000 | 0.972 | 0.979 | 153.43 | 0 |
| `eval_20260730T105915336204Z` | Rerank depth 20 | reranked | 1.000 | 0.986 | 0.990 | 6437.37 | 0 |
| `eval_20260730T110400722276Z` | Korean n-gram OR query fix | lexical | 1.000 | 1.000 | 1.000 | 49.04 | 0 |
| `eval_20260730T110400722276Z` | Corrected baseline plus RRF | hybrid | 1.000 | 0.986 | 0.990 | 87.06 | 0 |
| `eval_20260730T110400722276Z` | Corrected baseline, depth 20 | reranked | 1.000 | 0.986 | 0.990 | 6622.12 | 0 |
| `eval_20260730T110743478291Z` | Rerank depth 6 | reranked | 1.000 | 0.986 | 0.990 | 1847.64 | 0 |
| `eval_20260730T111540556068Z` | Unwarmed MPS compile exposure | reranked | 1.000 | 0.986 | 0.990 | 7733.97 | 0 |
| `eval_20260730T111926992603Z` | Final warmed scorecard | lexical | 1.000 | 1.000 | 1.000 | 19.76 | 0 |
| `eval_20260730T111926992603Z` | Final warmed scorecard | vector | 1.000 | 0.972 | 0.979 | 82.27 | 0 |
| `eval_20260730T111926992603Z` | Final warmed scorecard | hybrid | 1.000 | 0.986 | 0.990 | 79.33 | 0 |
| `eval_20260730T111926992603Z` | Final warmed scorecard | reranked | 1.000 | 0.986 | 0.990 | 1095.22 | 0 |

The first run exposed an AND-query failure: short keywords worked, while full
Korean questions returned no lexical candidates. The PostgreSQL adapter now
builds a safe OR websearch query from analyzed Korean n-grams. A real
PostgreSQL regression test protects this behavior.

Reducing the rerank depth from 20 to 6 preserved every measured rank. A later
unwarmed run exposed Apple MPS dynamic-compilation cost inside the timed
samples, so the evaluator now performs and records one untimed full-corpus
warmup pass per variant. The final warmed steady-state P95 is 1095.22 ms and
passes the two-second gate. Reranking still did not improve quality over
lexical, so the promotion decision remains `keep_disabled`.

## Jina Hugging Face trial: 2026-08-06

The optional local backend is configured with a pinned Jina v2 revision:

```toml
[models.reranker]
enabled = true
backend = "huggingface"
model = "jinaai/jina-reranker-v2-base-multilingual"
revision = "9cfeff2df7d40d1b78e75e5e9cebec92a99813c9"
max_length = 1024
device = "mps"
```

Install it with `uv sync --extra semantic` (or include `semantic` in the
`all` extra). The adapter is deliberately opt-in and uses the model's pinned
Transformers remote-code API. Review the model's `CC BY-NC 4.0` license before
any production or commercial deployment.

The fresh trial synced six PDFs into 70 current content units and evaluated
all 36 public-government cases after one untimed warmup pass. The Jina model
was exercised through the local Infinity sidecar for the full scorecard and
directly through the Hugging Face adapter in a service-level smoke test.

| Variant | Recall@10 | MRR | nDCG@10 | P95 ms | ACL leaks |
|---|---:|---:|---:|---:|---:|
| lexical | 1.000 | 0.986 | 0.990 | 9.04 | 0 |
| hybrid | 1.000 | 0.986 | 0.990 | 55.98 | 0 |
| Jina reranked | 0.972 | 0.972 | 0.972 | 613.31 | 0 |

`kip evaluate compare --baseline lexical --candidate reranked` returned
`keep_disabled`: overall recall delta `-0.0278`, semantic recall delta
`-0.0833`, exact regression `0.0000`; ACL, stale-source, and latency gates
passed. The failing case was `GOV-HI-005`, where Jina ranked the related
integrated-care document above the health-insurance document containing the
expected numeric expansion target. The report and decision artifact are
`evaluation/reports/jina-reranker-v2/latest.json` and
`evaluation/reports/jina-reranker-v2/decision.json`; the direct adapter smoke
is recorded in `evaluation/reports/jina-reranker-v2/huggingface-smoke.json`.

The result is a useful improvement in experimentability, not a quality
promotion: lexical remains the active default for this corpus until a larger,
harder internal golden set produces a gate-passing candidate.

## Commands

```bash
make fetch-corpus
./scripts/fetch_public_corpus.py --check
./scripts/kip sync run --source public-government
./scripts/semantic-server.sh run
./scripts/kip projection rebuild --name semantic
./scripts/kip projection verify --name semantic
make evaluate
./scripts/kip evaluate compare \
  --report evaluation/reports/public-government/latest.json \
  --baseline lexical \
  --candidate reranked
```

Use `./scripts/kip`, not a bare `python -m kip.cli`, so `.env` and the
PostgreSQL URL are loaded. Keep `public-government` and both model adapters
disabled in distributed example configuration until the operator explicitly
opts into this evaluation.

## Remaining evidence gaps

This six-document pilot is intentionally small and lexically distinctive. It
does not justify a claim that lexical retrieval will dominate on a private,
larger, or more paraphrase-heavy corpus. Before activation, extend the golden
set with explicitly allowlisted internal documents, confusing near-duplicates,
tables, document revisions, stale-source cases, and harder paraphrases. The
production reference path now uses HNSW; retain exact pgvector comparison runs
to measure ANN recall and ACL/freshness-filtered candidate sufficiency whenever
its parameters change.

## Quality control plane

Candidate comparisons are now declared with a versioned manifest rather than
an informal configuration change. The manifest pins the candidate component,
adapter, package or model revision, dataset, corpus, configuration, code
fingerprints, category coverage, latency ceiling, and evidence metrics.

```bash
./scripts/kip quality validate-manifest \
  --manifest evaluation/experiments/example.yaml

./scripts/kip quality recommend \
  --manifest evaluation/experiments/example.yaml \
  --report evaluation/reports/CANDIDATE/latest.json
```

`quality recommend` is deliberately read-only. It emits
`kip.quality-recommendation.v1` with machine-readable gates and either
`promote` or `keep_disabled`; it never activates a parser, embedding space, or
reranker. The operator must run the existing atomic activation surface after
reviewing the report.

Reviewed observations use `kip.evaluation-review-bundle.v1` and are bound to
the dataset name, immutable version, and source revision. Answer metrics cover
claim precision/recall, citation precision/recall, groundedness, completeness,
locator correctness, unsupported claims, and refusal appropriateness.
Ontology metrics cover entity, relation, and evidence precision/recall,
contradiction detection, path relevance/recall, temporal correctness,
duplicates, orphans, and ACL leakage. A missing review or metric is `null`,
never a pass. LLM-as-judge may run in shadow, but cannot create canonical truth
or auto-promote a candidate.

Datasets and cases declare `draft`, `reviewed`, `golden`, `challenge`, or
`canary`, plus split, version, reviewer role, and source revision. Promotion
requires a non-draft immutable dataset, a reviewed observation for every case
in every required dimension, perfect evidence/security gates, zero graph
integrity defects, and an actual retrieval improvement. Historical reports
without lifecycle metadata therefore fail closed.

The synthetic starter pair demonstrates the contract without claiming tenant
quality:

```bash
./scripts/kip evaluate validate \
  --dataset evaluation/golden/ontology-starter.yaml

./scripts/kip evaluate run \
  --dataset evaluation/golden/ontology-starter.yaml \
  --reviews evaluation/reviews/ontology-starter.yaml \
  --variants lexical,hybrid \
  --output-dir evaluation/reports/ontology-starter
```

Replace every synthetic public ID, source revision, expectation, and reviewed
observation with exact tenant evidence before treating a report as promotion
evidence. The CLI report deliberately omits reviewer identity.

Production telemetry is not automatically added to the golden set. A failed
query enters `golden`, `challenge`, or `canary` only after a reviewer records
the expected document/evidence, principal, ACL scopes, and source revision.
That change produces a new dataset fingerprint and invalidates stale
recommendations.

## Ontology release evaluation

Compare complete release roots before activating a new meaning contract:

```bash
./scripts/kip ontology validate --root ontology
./scripts/kip ontology diff \
  --before /path/to/ontology-1.0.0 \
  --after /path/to/ontology-1.1.0
```

Additions are compatible. Domain/range widening and policy tightening require
review. Symbol removal, parent changes, domain/range narrowing, and risk or
review weakening are breaking. Breaking changes require a reviewed
`kip.ontology-migration.v1` manifest passed with `--migration`; uncovered or
invalid symbols fail closed. Migration creates target-version candidates and
never rewrites approved assertions in place.
