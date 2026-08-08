# KIP Retrieval Evaluation

- Run: `eval_20260806T061559030906Z`
- Dataset: `public-government-ko-v1`
- Workspace: `default`
- Completed: `2026-08-06T06:16:28.099015Z`
- Untimed warmup passes: `1`

## Variant scorecard

| Variant | Cases | Recall@K | MRR | nDCG@K | Unauthorized | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| lexical | 36 | 100.0% | 98.6% | 99.0% | 0 | 6.58 | 9.04 |
| hybrid | 36 | 100.0% | 98.6% | 99.0% | 0 | 51.08 | 55.98 |
| reranked | 36 | 97.2% | 97.2% | 97.2% | 0 | 344.18 | 613.31 |

## Decision

Status: **baseline**

## Reproducibility fingerprints

- corpus: `sha256:68d47873a9c605021581b1522b6b6e0fd1e486cb6ee0cf82b4633e35a76fb6f8`
- dataset: `sha256:c61d3bb5c4b1306bf8ff1f69fc9bba54cc338c5136e7bcdc2b5c5aa5fbed2d08`
- configuration: `sha256:54f7ec0a0f90b83a8402e336a72f5a1287698142aae6240982dcf95ac4365ff3`
- code: `sha256:9f1e74315e5cf0fbd015c51ded3c9b7b9815ce8969cc795f2952ba75026ee5eb`
