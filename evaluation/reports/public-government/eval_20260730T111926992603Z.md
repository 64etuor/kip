# KIP Retrieval Evaluation

- Run: `eval_20260730T111926992603Z`
- Dataset: `public-government-ko-v1`
- Workspace: `default`
- Completed: `2026-07-30T11:20:50.441653Z`
- Untimed warmup passes: `1`

## Variant scorecard

| Variant | Cases | Recall@K | MRR | nDCG@K | Unauthorized | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| lexical | 36 | 100.0% | 100.0% | 100.0% | 0 | 17.86 | 19.76 |
| vector | 36 | 100.0% | 97.2% | 97.9% | 0 | 76.30 | 82.27 |
| hybrid | 36 | 100.0% | 98.6% | 99.0% | 0 | 74.72 | 79.33 |
| reranked | 36 | 100.0% | 98.6% | 99.0% | 0 | 808.88 | 1095.22 |

## Decision

Status: **baseline**

## Reproducibility fingerprints

- corpus: `sha256:68d47873a9c605021581b1522b6b6e0fd1e486cb6ee0cf82b4633e35a76fb6f8`
- dataset: `sha256:c61d3bb5c4b1306bf8ff1f69fc9bba54cc338c5136e7bcdc2b5c5aa5fbed2d08`
- configuration: `sha256:e667905120371566b1af5e1fac41a6d0b259889ee7c44f8865e0c72cc73311f9`
- code: `sha256:b31989422bd2693026d91f7d7862f0852accd3e204e945d513f01bf90f0e8e62`
