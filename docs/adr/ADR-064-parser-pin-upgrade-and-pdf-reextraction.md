# ADR-064: Upgrade pdf-inspector to 1.19.0 and Kordoc to 4.13.1 with PDF re-extraction

- **Status:** Accepted
- **Date:** 2026-09-11

## Context

`upstream-watch` reported `pdf-inspector` 1.14.2 -> 1.19.0 and Kordoc
4.8.0 -> 4.13.1. Both pins exist because parser output is evidence: a new
release can change page text, table units, OCR routing and therefore
retrieval, so a pin moves only with measured shadow evidence (ADR-053,
ADR-054).

Three gaps surfaced while measuring:

- `kip parser reextract` only scanned `.hwp`/`.hwpx`. The operations guide
  told operators to use it for PDF backend changes, but it scanned zero PDFs,
  so an existing deployment had no shadow path to adopt a PDF parser upgrade.
- pdf-inspector 1.19.0 preserves stroke-simulated bold and superscripts as
  inline Markdown (`**시범사업**을`, `<sup>*</sup>`). KIP's Korean n-gram
  analyzer and the BM25 lexical reranker treat `*` and tags as boundaries, so
  the markup split words and hid stem/suffix terms. Without a fix one public
  case fell from rank 1 to 2 (MRR 98.6% -> 97.2%).
- Deployment configs written by earlier setups carry
  `parsers.ocr.kordoc.expected_version = "4.8.0"` and are preserved by
  `kip update` (ADR-062). Changing the adapter pin alone would turn the
  one-command update into a configuration error until the operator edited the
  file by hand.

## Decision

- Pin `pdf-inspector==1.19.0`. KIP keeps calling only
  `extract_pages_markdown`; the optional OCR added upstream in 1.15
  (`process_pdf_with_ocr`) is not used, and OCR candidates still go to
  Kordoc. `pymupdf` remains the explicit rollback backend and the selective
  table fallback; there is still no automatic per-document PyMuPDF fallback.
- Search text drops presentation markup: `strip_inline_markup` removes paired
  Markdown emphasis (`*`, `**`, `***` on one line) and inline
  `u/sup/sub/b/i/em/strong/s/del/ins/mark/small` tags from pdf-inspector page
  and table `body_normalized`/`lexical_text` and from the reranker input of
  those units. Unpaired asterisks (masked `홍**동`, `010-****-1234`) stay, and
  other formats are scored verbatim because their `*` or `<b>` is content.
  `body`, which `read` and snippets use, keeps the extractor's Markdown exactly.
- `kip parser reextract` gains a repeatable `--extension` option (leading
  dot optional, case-insensitive). The default stays `.hwp`/`.hwpx`; any
  other explicitly requested suffix must have a registered parser or the
  command fails with a validation error (HWP/HWPX parsers match by file
  signature, so those two are accepted as the defaults they are). A run that
  finds no files of the requested formats warns. The summary reports
  `extensions`.
- Pin Kordoc 4.13.1 through `requirements/kordoc/` (overrides unchanged).
  `KORDOC_VERSION` is the single code constant; configs and setup write
  `4.13.1`. `resolve_kordoc_expected_version` maps a missing value or a pin
  that an earlier KIP release wrote (`4.7.3`, `4.8.0`) to the current pin,
  because the launcher always runs the runtime named by the manifest; any
  other value is still rejected.

## Evidence

Separate migrated PostgreSQL database, six licensed public PDFs, baseline
synced with 1.14.2 and the candidate activated with
`parser reextract --extension .pdf --activate`:

| Measure | 1.14.2 | 1.19.0 |
| --- | ---: | ---: |
| Documents / pages | 6 / 70 | 6 / 70 |
| Table units | 37 | 45 |
| PyMuPDF fallback table units / pages | 3 / 7 | 1 / 4 |
| OCR candidate pages / OCR units | 1 / 13 | 0 / 0 |
| Lexical Recall@10 / MRR / nDCG@10 | 100% / 98.6% / 99.0% | 100% / 98.6% / 99.0% |
| Unauthorized results | 0 | 0 |

The top-1 (document, page) hit is identical for 30/30 public relevance cases
and p50 latency stays about 34 ms. The garbled statistics page that 1.14.2
sent to OCR is recovered natively with its tables.

Kordoc: the lock change is only the `kordoc` package; the npm advisory set is
unchanged (the known moderate adm-zip chain). The `--format json --ocr
--silent` contract is identical; Korean scanned PDFs (200 and 150 dpi, with a
table) and an image-only PPTX OCR to character similarity 1.0000 on both
versions; HWPX JSON keeps `pageMode: section`. A real `./scripts/kip sync`
produced searchable `pdf_ocr`/`pptx_ocr` units read through the stdio MCP
server.

## Consequences

Existing indexes keep their 1.14.2 extractions until an operator runs the
PDF shadow re-extraction and activates it. The evidence covers six public
PDFs and synthetic scans; private-corpus table accuracy, OCR CER/WER on real
scans and semantic/reranked parity were not re-measured.

Rollback: `kip update --rollback` restores the 3.10.0 package files, but PDF
extractions activated with 1.19.0 stay active and 3.10.0 cannot shadow
re-extract PDFs (its `reextract` has no `--extension`); a changed file is
re-parsed on its next sync. To return PDF evidence to a known backend, set
`backend = "pymupdf"` and run `parser reextract --extension .pdf` (shadow,
then `--activate`) before rolling the release back, or keep 3.11.0 and pin
the backend there.
