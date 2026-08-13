# Qwen3 semantic shadow evaluation - 2026-08-13

Dataset: `evaluation/golden/private-onedrive-nl.yaml` (`reviewed` `1.0.0`,
19 cases). Run: `eval_20260812T151656562026Z`. Projection:
`espace_e3dedff83a62ec2de5f1fe86b0d89946`,
`qwen3-embedding-0.6b-1024-c4000-ht1`, complete at 30,565/30,565 current
active ACL-fresh units. The sidecar used Infinity 0.0.77 and the pinned Qwen3
0.6B revision on Apple MPS.

## Aggregate

| variant | Recall@10 | MRR | nDCG@10 | P95 | ACL leaks |
|---|---:|---:|---:|---:|---:|
| lexical | 0.789 | 0.646 | 0.680 | 7471.78 ms | 0 |
| vector | **0.947** | **0.822** | **0.853** | 9033.70 ms | 0 |
| hybrid | 0.895 | 0.702 | 0.750 | 8885.65 ms | 0 |
| reranked | 0.842 | 0.656 | 0.700 | 8704.71 ms | 0 |

Vector-only retrieval is the quality winner. Against lexical it improves
overall Recall@10 by `+0.158` and MRR by `+0.176`. Semantic-paraphrase
Recall@10 improves from `0.429` to `0.857` and MRR from `0.302` to `0.661`.
Exact-identifier and exact-numeric Recall@10 remain `1.000`; no variant emits
an unauthorized result or fails a case. The configured BM25 reranking path
reduces the semantic gain, so it is not the preferred composition for this
space.

## Gate reading

Decision: **keep_disabled**. The reviewed-dataset, overall-recall,
semantic-recall, exact-regression, unauthorized-result, and failed-case gates
pass. Vector P95 is `9033.70 ms` against the `2000 ms` ceiling, and this
retrieval-only dataset has no stale-warning observations, so that mandatory
gate is `null` and fails closed. The projection remains a complete shadow; no
activation command was run.

## Manual evidence check

For `SP-001`, the expected supplier-management document moves from lexical
rank 9 to vector rank 2 and hybrid rank 1. CLI exact-read reopened
`unit_aefb838cd2f3b8a06506c1007fabfd7e` at PDF page 6; the current and indexed
source hashes matched and `source_changed_since_index` was false.

## Reproducibility

- Corpus: `sha256:336dbd508736f26b49c4b75d29067989d3f0d1c48118504b7ff4d9cd555e416a`
- Dataset: `sha256:cf92919cf065c5bb279f27c8ce9b503da250852fc1a98c74149584ef7e6a03ed`
- Configuration: `sha256:02b5ba5464730ce52280e4e66595a48d73243ac68e150802e37bfbf97bc8a39f`
- Code: `sha256:85c259f63525e0a1182a671da28dadd7b0b620c5cae49168bb21c73fc5e79e68`
- Full report: `qwen3-head-tail/eval_20260812T151656562026Z.json`
