# ADR-054: Use pdf-inspector with selective PyMuPDF table fallback

- **Status:** Amended
- **Date:** 2026-08-17
- **Amended by:** ADR-064 (2026-09-11) — the pdf-inspector pin moved to 1.19.0
- **Scope:** accepted for the starter and pilot profile

## Context

The existing PDF adapter uses PyMuPDF for page text and
`find_tables(lines_strict)` on every page. Its page evidence is stable and its
simple bordered-table output is exact, but table discovery dominates ingest
time and cannot detect borderless tables. Firecrawl's local MIT-licensed
`pdf-inspector` 1.14.2 Rust engine provides page Markdown, layout/table signals,
and per-page OCR reasons from a prebuilt Python wheel without network or model
calls during parsing.

Three choices were measured: full replacement, classification-only use, and a
hybrid. Full replacement lost the exact 2x2 structure on a synthetic bordered
table. Classification alone kept the all-page PyMuPDF table cost. The hybrid
retains both strengths.

## Measured evidence

The same six public Korean government PDFs (70 pages) and 36-case reviewed
dataset were loaded into separate, freshly migrated PostgreSQL databases.

| Measure | PyMuPDF baseline | Hybrid candidate |
|---|---:|---:|
| Raw six-document parse | 11,327 ms | 589 ms |
| End-to-end sync | 15.58 s | 4.82 s |
| Page units | 70 | 70 |
| Table units | 44 | 37 |
| OCR units | 0 | 13 on one garbled page |
| Lexical Recall@10 | 1.0000 | 1.0000 |
| Lexical MRR | 0.9861 | 0.9861 |
| Unauthorized results | 0 | 0 |

The raw adapter was 19.2x faster and the isolated sync/database path was 3.23x
faster. A candidate-local BM25 extraction proxy also preserved Top-1 0.9667,
Recall@5 1.0, and MRR 0.9833. Explicit semantic/reranked evaluation was not
available because the local embedding sidecar was not running; both databases
failed that variant for the same environment reason.

Synthetic probes showed that the candidate detects a borderless table, rejects
a decorative contact box, preserves two-column reading order, and sends a
detected bordered table without valid Markdown through the existing exact
PyMuPDF fallback.

## Decision

1. Pin `pdf-inspector==1.14.2` in the extractor and production runtime profiles.
2. Make `[parsers.pdf].backend = "pdf_inspector"` the new starter default and
   retain `"pymupdf"` as the explicit rollback backend.
3. Emit one `pdf_page` unit per inspector page with the unchanged 1-indexed
   page locator. The body is structured Markdown rather than PyMuPDF plain text.
4. Promote valid consecutive Markdown table blocks to additive `pdf_table`
   units. For a page reported as containing a table but lacking valid Markdown
   table syntax, invoke PyMuPDF `lines_strict` for that page only.
5. Do not run fallback table extraction on pages the inspector marks for OCR.
   Inspector reasons plus KIP's existing low-text/PUA/control/replacement gate
   select Kordoc candidate pages; native page units remain present.
6. Load the optional native module only inside the adapter, require exact
   version 1.14.2, and convert native parse failures into typed KIP errors.
   Silent parser switching inside an extraction is forbidden.

## Consequences

- Normal retrieval still never triggers parsing or OCR.
- Page body bytes change when a source is re-extracted under the new backend;
  rollout must use shadow extraction and explicit activation for existing data.
- Markdown-derived table units have row/column counts and page/table locators
  but no bounding box. Fallback PyMuPDF tables retain their bbox.
- The candidate produced seven fewer table units on the public set. Several
  removed baseline units were garbled flowchart grids, but this is not a claim
  of complete table recall on private corpora.
- The public lexical gate passed; semantic/reranked parity, OCR CER/WER, private
  complex-table accuracy, memory peaks, encrypted PDFs, and full-corpus quality
  remain deployment-specific gates.

## References

- `src/kip/adapters/parsers/pdf_inspector.py`
- `src/kip/adapters/parsers/pdf_tables.py`
- `docs/adr/ADR-049-measured-pdf-tables-and-hwp-section-locators.md`
- Firecrawl pdf-inspector release `v1.14.2`, commit `4bee4f993ba28bd6a3334fa55e699b318663fba3`
