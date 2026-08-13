# KIP RAG Evaluation

- Run: `eval_20260813T043920194657Z`
- Dataset: `private-onedrive-nl`
- Dataset version: `1.0.0`
- Dataset lifecycle: `reviewed`
- Promotion eligible: `True`
- Workspace: `default`
- Completed: `2026-08-13T04:42:09.178076Z`
- Untimed warmup passes: `1`

## Variant scorecard

| Variant | Cases | Failures | Recall@K | MRR | nDCG@K | Unauthorized | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lexical | 19 | 0 | 78.9% | 64.6% | 68.0% | 0 | 5133.21 | 9945.74 |
| vector | 19 | 0 | 94.7% | 82.2% | 85.3% | 0 | 96.99 | 137.62 |

## Decision

Status: **baseline**

## Reproducibility fingerprints

- corpus: `sha256:336dbd508736f26b49c4b75d29067989d3f0d1c48118504b7ff4d9cd555e416a`
- dataset: `sha256:cf92919cf065c5bb279f27c8ce9b503da250852fc1a98c74149584ef7e6a03ed`
- configuration: `sha256:880ac3f38e59504440766a479f7032ae08a20161c1fafb635037a2894abc7c21`
- code: `sha256:8b83eddd94f45eb214439f592b135bd2e2b75a664b1443687da95bd35d956a4d`
