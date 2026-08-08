# KIP Retrieval Evaluation

- Run: `eval_20260806T072914413857Z`
- Dataset: `public-government-ko-v1`
- Workspace: `default`
- Completed: `2026-08-06T07:34:24.499297Z`
- Untimed warmup passes: `1`

## Variant scorecard

| Variant | Cases | Failures | Recall@K | MRR | nDCG@K | Unauthorized | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lexical | 36 | 0 | 100.0% | 93.5% | 95.0% | 0 | 531.38 | 679.13 |
| vector | 36 | 0 | 100.0% | 97.2% | 97.9% | 0 | 625.22 | 756.02 |
| hybrid | 36 | 0 | 100.0% | 98.6% | 99.0% | 0 | 592.59 | 760.66 |
| reranked | 36 | 0 | 100.0% | 98.6% | 99.0% | 0 | 1410.75 | 10029.13 |

## Decision

Status: **baseline**

## Reproducibility fingerprints

- corpus: `sha256:68d47873a9c605021581b1522b6b6e0fd1e486cb6ee0cf82b4633e35a76fb6f8`
- dataset: `sha256:c61d3bb5c4b1306bf8ff1f69fc9bba54cc338c5136e7bcdc2b5c5aa5fbed2d08`
- configuration: `sha256:a4897d46860430df697ff814c3889649ec0316fdb4deaaafc7480ef4fe616ae5`
- code: `sha256:e60d2548e0273bf77afee50a1d2356d8867203b0d8d4ab65921f0589a8f18212`
