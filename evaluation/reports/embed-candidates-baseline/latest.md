# KIP RAG Evaluation

- Run: `eval_20260912T072255289491Z`
- Dataset: `private-onedrive-nl`
- Dataset version: `1.0.0`
- Dataset lifecycle: `reviewed`
- Promotion eligible: `True`
- Workspace: `default`
- Completed: `2026-09-12T07:24:53.084356Z`
- Untimed warmup passes: `1`

## Variant scorecard

| Variant | Cases | Failures | Recall@K | MRR | nDCG@K | Unauthorized | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lexical | 19 | 0 | 89.5% | 63.8% | 70.2% | 0 | 1147.28 | 1670.00 |
| vector | 19 | 0 | 89.5% | 83.3% | 84.9% | 0 | 68.21 | 80.76 |
| hybrid | 19 | 0 | 89.5% | 89.5% | 89.5% | 0 | 1268.13 | 1785.43 |

## Decision

Status: **baseline**

## Reproducibility fingerprints

- corpus: `sha256:336dbd508736f26b49c4b75d29067989d3f0d1c48118504b7ff4d9cd555e416a`
- dataset: `sha256:cf92919cf065c5bb279f27c8ce9b503da250852fc1a98c74149584ef7e6a03ed`
- configuration: `sha256:74cba4ec30f2a5dc4216055e42f7b53bf46722a1089aa45013ca899f84fa5688`
- code: `sha256:92aeb343934f3b10d0c873fa810d260a6e48aa4d0c74699042c1e6cba1c17456`
