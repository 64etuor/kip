# Alias query expansion A/B — 2026-08-11

Feature: human-approved ontology aliases expand lexical candidate
retrieval (`search.alias_expansion_enabled`). Four seeded Concept
entities (협력업체/공급업체/거래처/벤더, 내부심사/내부감사,
전자출판물/전자책, 공장가동승인/양산승인) — all reviewed business
synonyms, created through `kip ontology entity-create` with the ACL
prefilter in `resolve_entities` applying at query time.

Three expansion strategies were measured against the 19-case grounded
draft set, per reranker backend:

| strategy | rapidfuzz recall/MRR | bm25 recall/MRR |
|---|---|---|
| none | 0.684 / 0.566 | 0.842 / 0.639 |
| full aliases (candidates + rerank query) | 0.737 / 0.524 | 0.842 / 0.632 |
| canonical-only (candidates + rerank query) | 0.737 / 0.550 | 0.789* / 0.579* |
| canonical, candidates-only (shipped) | 0.737 / 0.550 | 0.789* / 0.576* |

`*` includes one statement-timeout failure counted as recall 0; the same
query returns the expected document at rank 1 when re-run interactively.

## Findings

1. Under the **active rapidfuzz** configuration, expansion is a clear
   win: overall recall +0.053, semantic_paraphrase recall 0.286 → 0.429
   (공급업체→협력업체 and 전자책→전자출판물 both recovered), at a small
   MRR cost (-0.016). The shipped default keeps expansion enabled.
2. Under the **bm25 shadow candidate**, expansion is neutral-to-negative:
   bm25's reranking had already promoted those documents, and widening
   the candidate set shifts its candidate-local df statistics (SP-001
   rank 2 → 8). If bm25 is promoted, re-evaluate expansion together —
   or move bm25 to corpus-global df first.
3. Injecting synonyms into the **rerank query** measurably promotes
   synonym-dense but off-target documents; the shipped implementation
   expands candidate retrieval only and reranks against the user's
   original wording.
4. Expansion lengthens the websearch OR expansion, which pushed two
   long-sentence queries over the 15s statement timeout during eval runs
   (zero timeouts without expansion). Bounding long-query n-gram
   expansion is now the top search-infrastructure follow-up.
