# KIP RAG Evaluation

- Run: `eval_20260812T151656562026Z`
- Dataset: `private-onedrive-nl`
- Dataset version: `1.0.0`
- Dataset lifecycle: `reviewed`
- Promotion eligible: `True`
- Workspace: `default`
- Completed: `2026-08-12T15:31:31.782816Z`
- Untimed warmup passes: `1`

## Variant scorecard

| Variant | Cases | Failures | Recall@K | MRR | nDCG@K | Unauthorized | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lexical | 19 | 0 | 78.9% | 64.6% | 68.0% | 0 | 4621.01 | 7471.78 |
| vector | 19 | 0 | 94.7% | 82.2% | 85.3% | 0 | 6442.72 | 9033.70 |
| hybrid | 19 | 0 | 89.5% | 70.2% | 75.0% | 0 | 6497.26 | 8885.65 |
| reranked | 19 | 0 | 84.2% | 65.6% | 70.0% | 0 | 6399.30 | 8704.71 |

## Decision

Status: **baseline**

## Reproducibility fingerprints

- corpus: `sha256:336dbd508736f26b49c4b75d29067989d3f0d1c48118504b7ff4d9cd555e416a`
- dataset: `sha256:cf92919cf065c5bb279f27c8ce9b503da250852fc1a98c74149584ef7e6a03ed`
- configuration: `sha256:02b5ba5464730ce52280e4e66595a48d73243ac68e150802e37bfbf97bc8a39f`
- code: `sha256:85c259f63525e0a1182a671da28dadd7b0b620c5cae49168bb21c73fc5e79e68`
