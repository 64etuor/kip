# KIP RAG Evaluation

- Run: `eval_20260813T040321910277Z`
- Dataset: `private-onedrive-nl`
- Dataset version: `1.0.0`
- Dataset lifecycle: `reviewed`
- Promotion eligible: `True`
- Workspace: `default`
- Completed: `2026-08-13T04:27:46.151579Z`
- Untimed warmup passes: `1`

## Variant scorecard

| Variant | Cases | Failures | Recall@K | MRR | nDCG@K | Unauthorized | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lexical | 19 | 0 | 78.9% | 64.6% | 68.0% | 0 | 7757.23 | 10395.97 |
| vector | 19 | 1 | 89.5% | 79.6% | 82.0% | 0 | 14422.11 | 22260.35 |
| hybrid | 19 | 0 | 89.5% | 70.2% | 75.0% | 0 | 10652.50 | 17975.83 |
| reranked | 19 | 0 | 84.2% | 65.6% | 70.0% | 0 | 7703.16 | 13908.78 |

## Decision

Status: **incomplete**

- one or more evaluation cases failed

## Reproducibility fingerprints

- corpus: `sha256:336dbd508736f26b49c4b75d29067989d3f0d1c48118504b7ff4d9cd555e416a`
- dataset: `sha256:cf92919cf065c5bb279f27c8ce9b503da250852fc1a98c74149584ef7e6a03ed`
- configuration: `sha256:880ac3f38e59504440766a479f7032ae08a20161c1fafb635037a2894abc7c21`
- code: `sha256:0f9e1ec11e217910266c36a3ea2f7562b2548d4d0497902032c688f5773eb04d`
