# PDF Inspector Hybrid Parser Design

## Goal

Adopt Firecrawl `pdf-inspector` 1.14.2 as KIP's default local PDF page and OCR
routing engine without losing the exact bordered-table evidence currently
provided by PyMuPDF.

## Evidence

The evaluation used the six checked-in public Korean government PDFs (70
pages), the 30 positive questions in `public-government.yaml`, and synthetic
bordered, borderless, decorative-box, and two-column probes.

| Measure | Current PyMuPDF | pdf-inspector | Selected hybrid estimate |
|---|---:|---:|---:|
| Six-document parse | 11,761 ms | 277 ms | 590 ms |
| Relative speed | 1.0x | 42.4x | 19.9x |
| Page-count agreement | baseline | 70/70 | 70/70 |
| BM25 Top-1 / MRR | 0.9667 / 0.9833 | 0.9667 / 0.9833 | expected same page content |
| OCR candidates | 0 | 1 valid garbled-text page | inspector routing |

`pdf-inspector` found borderless tables and avoided a decorative-box false
positive. It detected the simple bordered-table page but did not render that
probe as Markdown, while PyMuPDF produced the exact 2x2 table. Therefore a full
replacement would regress an existing evidence contract.

## Considered approaches

1. **Replace PyMuPDF completely.** Fastest, but rejected because a detected
   bordered table may lack structured Markdown and the `pdf_table` contract
   would regress.
2. **Use pdf-inspector only for classification.** Lowest behavioral risk, but
   rejected because all-page PyMuPDF table discovery still dominates runtime.
3. **Hybrid page extraction and selective exact-table fallback.** Selected.
   Promote valid Markdown table blocks directly; only pages reported as table
   pages without a Markdown table run PyMuPDF `lines_strict` fallback.

## Architecture

- `PdfInspectorParser` is an adapter under `src/kip/adapters/parsers/`; domain,
  application, CLI, REST, and MCP contracts do not import the vendor package.
- Python package `pdf-inspector==1.14.2` belongs to the `extractors` and `all`
  extras and is loaded dynamically. Core-wheel startup remains independent of
  optional extractors.
- `[parsers.pdf].backend` selects `pdf_inspector` (new default) or `pymupdf`
  (rollback). Unknown values fail configuration parsing.
- Each inspector page becomes one `pdf_page` unit with the same 1-indexed page
  locator. Body text becomes the page's structured Markdown.
- Consecutive valid Markdown-table lines become additive `pdf_table` units.
  A table-detected page with no valid Markdown table falls back to the current
  PyMuPDF exact-table helper for that page only.
- Inspector `needs_ocr` and machine-readable reasons drive page candidates;
  KIP's existing low-text/PUA/control/replacement check remains a conservative
  secondary gate. Kordoc OCR still runs offline and only its candidate pages
  are admitted.
- Any inspector parse failure is a typed `ParserError`; no silent switch to a
  second parser occurs inside one extraction. Operators use the explicit
  `pymupdf` backend for rollback, preserving shadow-extraction semantics.

## Security and operations

The Rust extension is local, MIT licensed, performs no model or network calls,
and version 1.14.2 adds bounds for pathological Form XObjects, CMaps, content
streams, detector lookback, and rectangle clustering. Source files remain
read-only. The parser-worker process limits remain the outer CPU/RSS/wall and
response boundary.

## Acceptance

- Synthetic contracts cover page locators, Markdown tables, bordered-table
  fallback, decorative boxes, OCR routing, dependency/version failure, and
  explicit backend selection.
- The public six-document baseline and candidate run in separate migrated
  PostgreSQL databases, using real sync and evaluation commands.
- Candidate must preserve 30-question retrieval metrics, emit no ACL leaks,
  materially reduce parsing time, and pass the full repository verification.

