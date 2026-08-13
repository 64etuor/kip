# Qwen3 HNSW semantic shadow decision - 2026-08-13

Dataset: `evaluation/golden/private-onedrive-nl.yaml` (`reviewed` `1.0.0`,
19 cases). Run: `eval_20260813T043920194657Z`. Projection:
`espace_e3dedff83a62ec2de5f1fe86b0d89946`, complete at 30,565/30,565 current
active ACL-fresh units. Runtime: Infinity 0.0.77, pinned Qwen3 Embedding 0.6B
revision, Apple MPS, PostgreSQL 18, pgvector 0.8.2, and migration 0018 HNSW.

## Aggregate

| variant | Recall@10 | MRR | nDCG@10 | P50 | P95 | failures | ACL leaks |
|---|---:|---:|---:|---:|---:|---:|---:|
| lexical | 0.789 | 0.646 | 0.680 | 5133.21 ms | 9945.74 ms | 0 | 0 |
| vector | **0.947** | **0.822** | **0.853** | **96.99 ms** | **137.62 ms** | 0 | 0 |

Vector improves overall Recall@10 by `+0.158`, MRR by `+0.176`, and
semantic-paraphrase Recall@10 by `+0.429` (`0.429 -> 0.857`). Exact-identifier
and exact-numeric Recall@10 remain `1.000`. The HNSW result exactly matches the
prior exact-search category and aggregate quality metrics.

## Root cause and correction

The first HNSW evaluation still used `ORDER BY distance, unit_id` inside the
vector candidate query. PostgreSQL could not satisfy that ordering with the
single-column HNSW operator index, so it sequentially scanned 30,565 vectors,
joined every eligible unit, and then sorted. One case exceeded the 15-second
statement timeout and the observed vector P95 was 22,260.35 ms.

The corrected query makes distance the HNSW ordering key, retains ACL,
freshness, current-revision, source-hash, and request filters as a correlated
`EXISTS` filter on the index scan, and prevents planner decorrelation. Strict
iterative scan continues until the requested authorized candidates are found.
`EXPLAIN ANALYZE` selected `embeddings_1024_hnsw_cosine_idx`; the final report
has no failed case and P95 137.62 ms.

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
- Code: `sha256:8b83eddd94f45eb214439f592b135bd2e2b75a664b1443687da95bd35d956a4d`
- Full report: `eval_20260813T043920194657Z.json`
