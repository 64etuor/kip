# Qwen3 HNSW all-mode semantic shadow decision - 2026-08-13

Dataset: `evaluation/golden/private-onedrive-nl.yaml` (`reviewed` `1.0.0`,
19 cases). Run: `eval_20260813T045409019529Z`. Projection:
`espace_e3dedff83a62ec2de5f1fe86b0d89946`, complete at 30,565/30,565 current
active ACL-fresh units. Runtime: Infinity 0.0.77, pinned Qwen3 Embedding 0.6B
revision, Apple MPS, PostgreSQL 18, pgvector 0.8.2, and migration 0018 HNSW.

## Aggregate

| variant | Recall@10 | MRR | nDCG@10 | P50 | P95 | failures | ACL leaks |
|---|---:|---:|---:|---:|---:|---:|---:|
| lexical | 0.789 | 0.646 | 0.680 | 4725.70 ms | 7385.80 ms | 0 | 0 |
| vector | **0.947** | **0.822** | **0.853** | **95.29 ms** | **133.75 ms** | 0 | 0 |
| hybrid | 0.895 | 0.702 | 0.750 | 4834.54 ms | 7924.98 ms | 0 | 0 |
| reranked | 0.842 | 0.656 | 0.700 | 5801.17 ms | 15512.58 ms | 0 | 0 |

Vector is the measured winner. Against lexical it improves overall Recall@10
by `+0.158`, MRR by `+0.176`, and semantic-paraphrase Recall@10 by `+0.429`
(`0.429 -> 0.857`). Exact-identifier and exact-numeric Recall@10 remain
`1.000`. HNSW preserves the previous exact-search aggregate and category
quality metrics.

Hybrid inherits the slow lexical channel and weakens the vector ranking.
Candidate-local BM25 reranking weakens it further and is the slowest mode on
this corpus. Neither is the promotion candidate for this embedding space.

## Root cause and correction

The first HNSW evaluation still used `ORDER BY distance, unit_id` inside the
vector candidate query. PostgreSQL could not satisfy that ordering with the
single-column HNSW operator index, so it sequentially scanned 30,565 vectors,
joined every eligible unit, and then sorted. One case exceeded the 15-second
statement timeout and vector P95 was 22,260.35 ms.

The corrected query makes distance the HNSW ordering key, retains ACL,
freshness, current-revision, source-hash, and request filters as a correlated
`EXISTS` filter on the index scan, and prevents planner decorrelation. Strict
iterative scan continues until the requested authorized candidates are found.
`EXPLAIN ANALYZE` selected `embeddings_1024_hnsw_cosine_idx`.

## Gate reading

Decision: **keep_disabled**. Reviewed dataset, overall-recall improvement,
semantic-recall improvement, exact regression, unauthorized results, latency,
and failed-case gates pass. `stale_warning_rate` is `null` because this
retrieval-only dataset contains no stale-source observation; the mandatory
freshness gate therefore fails closed. No activation command ran.

This is strong private-corpus retrieval evidence, not an industry-ranking or
end-to-end answer-quality claim. The set has only 19 reviewed questions and no
frozen holdout.

## Reproducibility

- Corpus: `sha256:336dbd508736f26b49c4b75d29067989d3f0d1c48118504b7ff4d9cd555e416a`
- Dataset: `sha256:cf92919cf065c5bb279f27c8ce9b503da250852fc1a98c74149584ef7e6a03ed`
- Configuration: `sha256:880ac3f38e59504440766a479f7032ae08a20161c1fafb635037a2894abc7c21`
- Code: `sha256:15de765d07b3e01758077237d4f0165d955242841bbe939f9da3715f8953dd7a`
- Full report: `eval_20260813T045409019529Z.json`
