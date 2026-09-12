# KIP RAG Evaluation

- Run: `eval_20260912T102459163913Z`
- Dataset: `private-onedrive-nl`
- Dataset version: `1.0.0`
- Dataset lifecycle: `reviewed`
- Promotion eligible: `True`
- Workspace: `default`
- Completed: `2026-09-12T10:26:54.600787Z`
- Untimed warmup passes: `1`

## Variant scorecard

| Variant | Cases | Failures | Recall@K | MRR | nDCG@K | Unauthorized | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lexical | 19 | 0 | 89.5% | 63.8% | 70.2% | 0 | 1178.69 | 1730.51 |
| vector | 19 | 0 | 100.0% | 67.9% | 75.8% | 0 | 55.29 | 65.15 |
| hybrid | 19 | 0 | 94.7% | 80.6% | 84.0% | 0 | 1254.13 | 1743.11 |

## Decision

Status: **baseline**

## Reproducibility fingerprints

- corpus: `sha256:336dbd508736f26b49c4b75d29067989d3f0d1c48118504b7ff4d9cd555e416a`
- dataset: `sha256:cf92919cf065c5bb279f27c8ce9b503da250852fc1a98c74149584ef7e6a03ed`
- configuration: `sha256:b305dde62f43e7596dfd162c6d198d12845d29a7539432caf59b45890c1ccb02`
- code: `sha256:1aacf4f67087f8174d7f4e3661bf3482d067622079a524644214914a5c3552cb`
