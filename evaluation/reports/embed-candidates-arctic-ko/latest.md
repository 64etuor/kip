# KIP RAG Evaluation

- Run: `eval_20260912T085507912514Z`
- Dataset: `private-onedrive-nl`
- Dataset version: `1.0.0`
- Dataset lifecycle: `reviewed`
- Promotion eligible: `True`
- Workspace: `default`
- Completed: `2026-09-12T08:57:07.393014Z`
- Untimed warmup passes: `1`

## Variant scorecard

| Variant | Cases | Failures | Recall@K | MRR | nDCG@K | Unauthorized | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lexical | 19 | 0 | 89.5% | 63.8% | 70.2% | 0 | 1174.12 | 1747.10 |
| vector | 19 | 0 | 89.5% | 67.5% | 73.1% | 0 | 61.63 | 67.93 |
| hybrid | 19 | 0 | 89.5% | 80.3% | 82.6% | 0 | 1217.60 | 1756.59 |

## Decision

Status: **baseline**

## Reproducibility fingerprints

- corpus: `sha256:336dbd508736f26b49c4b75d29067989d3f0d1c48118504b7ff4d9cd555e416a`
- dataset: `sha256:cf92919cf065c5bb279f27c8ce9b503da250852fc1a98c74149584ef7e6a03ed`
- configuration: `sha256:289cb4a47626f7cab4f430fdcd287f63ca8698cbf089d88e7245f49fbf9a5f36`
- code: `sha256:36c86a65eb28c2a1a7b5caaed1e0dcde474c5f9599074010b6256404be86f724`
