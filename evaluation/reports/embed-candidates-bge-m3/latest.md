# KIP RAG Evaluation

- Run: `eval_20260912T115142925060Z`
- Dataset: `private-onedrive-nl`
- Dataset version: `1.0.0`
- Dataset lifecycle: `reviewed`
- Promotion eligible: `True`
- Workspace: `default`
- Completed: `2026-09-12T11:54:06.716430Z`
- Untimed warmup passes: `1`

## Variant scorecard

| Variant | Cases | Failures | Recall@K | MRR | nDCG@K | Unauthorized | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lexical | 19 | 0 | 89.5% | 63.8% | 70.2% | 0 | 1271.28 | 1852.68 |
| vector | 19 | 0 | 89.5% | 71.1% | 75.8% | 0 | 49.53 | 57.55 |
| hybrid | 19 | 0 | 89.5% | 80.0% | 82.4% | 0 | 1804.85 | 2918.95 |

## Decision

Status: **baseline**

## Reproducibility fingerprints

- corpus: `sha256:336dbd508736f26b49c4b75d29067989d3f0d1c48118504b7ff4d9cd555e416a`
- dataset: `sha256:cf92919cf065c5bb279f27c8ce9b503da250852fc1a98c74149584ef7e6a03ed`
- configuration: `sha256:0eaa1c89b6da9778f2a022e9198f302ddbdfe6c92e0c088ff1821920042a7c06`
- code: `sha256:9bf46fe5f7d00a9598ac1d51236f6cc3f3189274665b19ae830924e7a0fc7b61`
