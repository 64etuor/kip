# Manual QA - 2026-08-13

Surface: actual `./scripts/kip` CLI against the local PostgreSQL corpus and the
foreground loopback Infinity sidecar. Generation was disabled, so answer runs
used only local exact-evidence/refusal behavior.

## Previously failing answer scenarios

| Case | Observed result |
|---|---|
| N2 carbon-emissions owner absent from the named report | `refused=true`, `answer_not_present`, zero citations |
| N3 lunch menu absent from the named report | `refused=true`, `answer_not_present`, zero citations |
| N4 Mars contact network absent from the named report | `refused=true`, `answer_not_present`, zero citations |
| A1 `자격인증` | `refused=true`, `clarification_required` |
| A2 `평가 Report` | `refused=true`, `clarification_required` |
| A3 `자격인증 평가` | `refused=true`, `clarification_required` |
| A4 `Report` | `refused=true`, `clarification_required` |

All commands exited 0 with `kip.envelope.v1`, `ok=true`. A successful envelope
is expected for a typed product refusal; `data.refused` and
`data.refusal_reason` are the semantic outcome.

## Context-free code-switch and ACL scenarios

| Scenario | Lexical | Vector HNSW |
|---|---:|---:|
| EN-001 Korean-English retention-period query | miss in top 10 | expected document rank 1 |
| EN-002 Korean-English safety-training query | expected document rank 1 | expected document rank 1 |
| Explicit outsider scope, known private identifier | not evaluated here | zero results |

The EN-001 result demonstrates the measured remaining lexical candidate-recall
limit and the value of the semantic channel. It does not justify activation
without stale-source evidence.

## Sidecar and query evidence

- `GET /models` returned both `kip-qwen3-embedding-0.6b` and
  `kip-bge-reranker-v2-m3`.
- Real `/embeddings` requests returned HTTP 200 throughout the evaluations.
- A direct warm embedding request was about 0.1 seconds.
- `EXPLAIN ANALYZE` selected
  `embeddings_1024_hnsw_cosine_idx` with ACL/freshness/current-revision/source
  hash filters applied before the candidate limit.
- The final all-mode 19-case vector run completed with zero failures, zero ACL
  leaks, P50 95.29 ms, and P95 133.75 ms.
