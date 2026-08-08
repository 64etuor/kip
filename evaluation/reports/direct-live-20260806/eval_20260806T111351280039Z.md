# KIP Retrieval Evaluation

- Run: `eval_20260806T111351280039Z`
- Dataset: `public-government-ko-v1`
- Workspace: `default`
- Completed: `2026-08-06T11:18:50.310529Z`
- Untimed warmup passes: `1`

## Variant scorecard

| Variant | Cases | Failures | Recall@K | MRR | nDCG@K | Unauthorized | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lexical | 36 | 0 | 100.0% | 93.5% | 95.0% | 0 | 502.89 | 637.59 |
| vector | 36 | 0 | 100.0% | 97.2% | 97.9% | 0 | 578.54 | 737.28 |
| hybrid | 36 | 0 | 100.0% | 98.6% | 99.0% | 0 | 574.89 | 717.06 |
| reranked | 36 | 24 | 33.3% | 33.3% | 33.3% | 0 | 559.66 | 10394.87 |

## Decision

Status: **incomplete**

- one or more evaluation cases failed

## Reproducibility fingerprints

- corpus: `sha256:68d47873a9c605021581b1522b6b6e0fd1e486cb6ee0cf82b4633e35a76fb6f8`
- dataset: `sha256:c61d3bb5c4b1306bf8ff1f69fc9bba54cc338c5136e7bcdc2b5c5aa5fbed2d08`
- configuration: `sha256:a4897d46860430df697ff814c3889649ec0316fdb4deaaafc7480ef4fe616ae5`
- code: `sha256:e928d33c685e3503a51eeb360953744abe686869046d3ef7637a6a1a1b8660f6`
