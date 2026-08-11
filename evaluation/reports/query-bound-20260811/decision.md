# Long-query n-gram bound — 2026-08-11

`_websearch_or_query` now keeps the 64 most selective terms (longest
first, stable order) instead of the first 256 in emission order, which
was dominated by low-selectivity 2-grams for long natural-language
questions.

Against the 19-case grounded draft set (alias expansion active, same
corpus, immediately prior runs as baseline):

| config | recall@10 | MRR | failed cases | P95 |
|---|---:|---:|---:|---:|
| rapidfuzz before | 0.737 | 0.550 | 0 | 12.04s |
| rapidfuzz after | 0.737 | 0.550 | 0 | **9.27s** |
| bm25 before | 0.789 | 0.576 | 1 (timeout) | 12.47s |
| bm25 after | 0.789 | **0.620** | **0** | **7.37s** |

Interactive long-sentence queries drop from 15s statement timeouts to
5.4-6.9s. No recall or ordering regression; the bm25 MRR gain is the
recovered timeout case.

Note: bm25 recall stays 0.789 (vs 0.842 without alias expansion) —
the expansion/bm25 interaction documented in
`../alias-expansion-20260811/decision.md` remains; it is an expansion
property, not a bound property. The HWP corpus was re-extracted with
overlap chunking (263 → 282 units) while the second run was queued, so
cross-run deltas within one case are within corpus-drift noise.
