# ADR-018: Jina Hugging Face reranker remains an opt-in shadow adapter

- Status: Accepted for shadow evaluation — production-default claim
  superseded by ADR-034 (BM25 is the promoted default backend; RapidFuzz is
  the fallback)
- Date: 2026-08-06
- Re-test trigger: re-run the reranker comparison when the corpus grows
  materially past the 36-case/70-unit evaluation this ADR rests on
  (the private corpus is already 30,000+ units) or when a candidate
  reranker model is upgraded; use the judge-draft pipeline (ADR-045) to
  grow the reviewed comparison set instead of re-using the dated one.

## Context

KIP already has a loopback HTTP reranker path. We need a reproducible local
Hugging Face path for testing stronger multilingual rerankers without moving
model or vendor dependencies into the domain/application layers. Reranking
also fetched each candidate content unit separately, which added avoidable
database round trips and risked making the ACL boundary harder to audit.

## Decision

- Add `HuggingFaceJinaRerankerAdapter` under `src/kip/adapters/rerankers/`.
  It loads `jinaai/jina-reranker-v2-base-multilingual` through the pinned
  Transformers API, validates score cardinality and finiteness, and maps
  runtime/model failures to the existing dependency-unavailable fallback.
- Select the adapter only when `[models.reranker].backend = "huggingface"`.
  The model revision must be pinned and `trust_remote_code` is used only for
  that pinned model load. The model card is licensed `CC BY-NC 4.0`; production
  or commercial use requires an explicit license review.
- Add `RepositoryPort.get_content_units` and use one ACL-filtered bulk lookup
  for rerank candidates. PostgreSQL preserves requested order and rejects any
  missing or unauthorized candidate; memory and PostgreSQL adapters retain the
  existing exact-read semantics.
- Keep semantic/reranked retrieval shadow-only by default. An experiment is
  promotable only when `kip evaluate compare` passes quality, exact-regression,
  ACL, stale-source, and latency gates.

## Evidence

The 2026-08-06 public-government trial used six PDFs, 36 golden cases, and 70
current content units after sync. The Jina model was served locally by the
Infinity sidecar and was also exercised directly through the Hugging Face
adapter. The current composition boundary is `Application.retrieval`; the
former `KnowledgeService` symbol referenced by the original experiment no
longer exists.

| Variant | Recall@10 | MRR | nDCG@10 | P95 ms | ACL leaks |
|---|---:|---:|---:|---:|---:|
| lexical | 1.000 | 0.986 | 0.990 | 9.04 | 0 |
| hybrid | 1.000 | 0.986 | 0.990 | 55.98 | 0 |
| Jina reranked | 0.972 | 0.972 | 0.972 | 613.31 | 0 |

The candidate failed both overall-recall and semantic-recall promotion gates.
`GOV-HI-005` is the concrete regression: Jina preferred a related
integrated-care document over the health-insurance document containing the
exact `12개 → 57+α개 시군구` target. The complete machine-readable evidence is
under `evaluation/reports/jina-reranker-v2/` and the append-only comparison is
in `evaluation/reports/evolution.jsonl`.

## Consequences

Jina is available for repeatable local experiments and future corpus-specific
calibration, but the safe production choice for this corpus remains the
corrected lexical path, with hybrid/reranking available as explicit shadow
variants. The bulk candidate lookup reduces reranking database I/O without
changing source immutability or ACL semantics.
