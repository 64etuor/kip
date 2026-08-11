# Reranker A/B on the grounded NL draft set — 2026-08-11

Dataset: `evaluation/golden/private-onedrive-nl-draft.yaml` (19 cases, 13
documents; every question derived from a verbatim quote read from the
expected document's evidence unit; three cases hash-spot-verified).
Runs: `norerank/`, `rapidfuzz/`, `bm25/` under identical corpus and code;
only `models.reranker.backend` / `search.lexical_rerank_enabled` differ.

## Aggregate

| config | Recall@10 | MRR | nDCG@10 | P95 |
|---|---:|---:|---:|---:|
| no rerank | 0.737 | 0.471 | 0.537 | 9.97s |
| rapidfuzz (active) | 0.684 | 0.566 | 0.596 | 11.12s |
| bm25 (candidate) | **0.842** | **0.639** | **0.689** | 8.95s |

## By category (recall / MRR)

| category | n | no rerank | rapidfuzz | bm25 |
|---|---:|---|---|---|
| exact_identifier | 2 | 1.000 / 0.333 | 1.000 / 0.500 | 1.000 / 0.500 |
| exact_numeric | 3 | 1.000 / 0.583 | 1.000 / 0.750 | 1.000 / **1.000** |
| natural_language | 7 | 1.000 / 0.648 | 0.857 / 0.786 | **1.000 / 0.878** |
| semantic_paraphrase | 7 | 0.286 / 0.286 | 0.286 / 0.286 | **0.571** / 0.286 |

Notable: rapidfuzz pushed one relevant NL document out of the top-10
(recall regression vs no-rerank); bm25 recovered it and additionally
promoted two paraphrase documents from ranks 11-40 into the top-10.
Per-case: bm25 improves 5 cases (EN-002, NL-004, NL-006, SP-001, SP-002)
and regresses 2 (NL-005 문서화된 정보 관리, SP-007 전자책 심사위원 규모).

## Gate reading

Against the runner's activation thresholds, bm25 vs the active rapidfuzz
would pass mechanically: overall recall +0.158 (gate ≥ +0.03), semantic
category recall +0.285 (gate ≥ +0.10), no category-level regression, and
lower latency. **Promotion is still blocked on purpose**: the dataset
lifecycle is `draft` because the cases were machine-grounded and have not
been human-reviewed, and this repository does not allow the author of a
candidate to approve it. Reviewing the 19 cases (question wording,
expected document, category labels) and re-marking the dataset
`reviewed` with a version and reviewer is the remaining human step; the
config flip afterwards is `models.reranker.backend = "bm25"`.

## Known follow-ups

- Long full-sentence questions run 9-11s at P95 in-harness because the
  n-gram OR expansion grows with query length; interactive short queries
  run 0.7-2.6s. Bounding expanded query terms is a separate optimization.
- semantic_paraphrase recall is capped by lexical candidate generation
  (0.571 best); recovering the rest requires the semantic channel, not a
  reranker.
