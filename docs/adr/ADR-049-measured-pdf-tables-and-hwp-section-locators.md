# ADR-049: Measured PDF table extraction and HWP section locators

- **Status:** Accepted
- **Date:** 2026-08-16

## Context

An empirical parsing-capability review found two structure gaps with real
operational impact for Korean business documents:

1. **PDF tables were lost.** `pdf.py` extracted only `page.get_text("text")`,
   so 정산표/공문 표 collapsed into flowing text and cell relationships
   disappeared. Unlike XLSX (which has `kip xlsx-read` for exact ranges),
   PDF had no structural counterpart at all.
2. **HWP/HWPX evidence units had no real locators.** The `hwp_structure`
   locator carried `{"section": null, "chunk": N, "char_start": .., "char_end": ..}`,
   so a citation could not say which section a fact came from — the weakest
   evidence precision in the system, on the format that matters most here.

Rather than pick a library by reputation, both were decided by A/B
measurement against candidates that were **already pinned dependencies**, so
neither decision adds a dependency.

## Measured evidence

### PDF tables (synthetic ground-truth set + 6 real government PDFs, 70 pages)

| Metric | Baseline `get_text` | PyMuPDF `find_tables(lines_strict)` | kordoc 4.7.3 `--format json` |
|---|---|---|---|
| Bordered table cell precision/recall | n/a | **1.00 / 1.00** | 1.00 / 1.00 |
| Borderless (whitespace-aligned) table | n/a | **not detected (0)** | 0.875 / 0.875 |
| Merged/spanning header | n/a | span split, no colspan concept | span split; `colSpan`/`rowSpan` present but not merged |
| False tables, 2-column boxed prose probe | n/a | **0** | 0 |
| False tables, real narrative PDF (19 p) | n/a | 4 detected, all decorative 1-row boxes | **23 of 24 were 1×1 footer boxes (95.8%)** |
| Page-break table | n/a | 1.00/1.00 per physical page (never merges) | merges across pages into one logical table |
| Effect on existing page text | baseline | **byte-identical on 100% of files** | forcing tables off *breaks* reading order |
| Runtime (study set) | **3.4 ms/page** | 57.2 ms/page, in-process | ~57 ms/page **plus ~0.24–0.3 s fixed process start per document** |
| Runtime (post-implementation re-measure, same 6 real PDFs / 70 pages) | **3.95 ms/page** (0.28 s total) | **149.7 ms/page average, 61–468 ms/page per file** (10.5 s total) | not re-run |
| Encrypted PDF | clean typed failure | opens after `authenticate()` | **cannot open at all** (`--password` is HWP-family only) |
| Damaged PDF | partial recovery | partial recovery | **hard fail, exit 1, no output** |
| Image-only page | empty → existing OCR routing | 0 tables (no hallucination) | 0 tables |

The PyMuPDF `text` strategy was measured and rejected outright: it
hallucinated a 13×7 table on the prose probe and mis-shaped real bordered
tables.

### HWP/HWPX locators (real 기안문 HWPX templates + a synthetic 2-section file)

| Candidate | Section | Page | Cost |
|---|---|---|---|
| Native parser, public API | not exposed | none (format has no page data) | ~1–2 ms |
| **Native parser, per-section reconstruction** | **real index, verified byte-identical to `extract_text()`** | none | ~1–2 ms |
| Raw HWPX zip XML | real index | none | 0 deps, ~140 LOC to reimplement the library's walk |
| kordoc `--format json` | **never emits any section key** | `pageNumber` only; 100 % "section-approximate" mode on this corpus | ~139 ms/invocation (50–100× slower) |

A prior measured A/B (recorded in `docs/AI_OPERATOR_RUNBOOK.md`) showed
kordoc as the *primary* HWP parser regressing retrieval from Recall@5/MRR
1.00/1.00 to 0.444/0.444, which is why native remains primary.

## Decision

1. **Adopt PyMuPDF `find_tables(strategy="lines_strict")` as an additive
   `pdf_table` unit**, promoted only when `row_count >= 2 AND col_count >= 2`
   — the filter that suppresses the measured decorative-box false positives.
   `pdf_page` text stays byte-identical (measured side-effect-free), table
   detection failures degrade to a `TABLE_DETECTION_FAILED` warning, and the
   feature is config-gated (`[parsers.pdf] tables_enabled`, default on).
   **Cost correction:** re-measuring after implementation on the same six
   real PDFs gave **≈150 ms/page average (61–468 ms/page per file), ~38× the
   3.95 ms/page baseline** — materially higher than the study's 57 ms/page
   headline. The default stays on because ingestion is offline and never
   triggered by retrieval (rule 13) and tables were otherwise lost entirely,
   but operators with large PDF corpora should know the real number and can
   set `tables_enabled = false`.
2. **Reject kordoc for born-digital PDF tables.** Its JSON shape is richer
   (cross-page merging, span fields), but a 95.8 % false-table rate on a
   real document, inability to open encrypted PDFs, all-or-nothing failure
   on damaged files, and a fixed ~0.25 s per-document process cost are worse
   trade-offs than the gap being closed. Revisit only for pages already
   routed to OCR, where kordoc is the sole candidate that can see text.
3. **Fill `section` in `hwp_structure` locators in-process.** The
   reconstruction replays the dependency's own per-section extraction and is
   verified at runtime against its public `extract_text()`; on any mismatch
   the parser falls back to `section: null` with a
   `SECTION_INDEX_UNAVAILABLE` warning, so a wrong section number can never
   be emitted and the extracted text never changes.
   **Ordering correction (found during implementation):** the join must keep
   the dependency's own (lexical) file order, because that is the order the
   verified `extract_text()` output was built in — re-sorting numerically
   before joining breaks byte-identity for documents with ≥10 sections. Only
   the section *label* is computed numerically, so a chunk inside
   `section10.xml` is labeled `10` even though that file sorts before
   `section2.xml`.
   Losing the section label is reported as a warning but does **not**
   downgrade the extraction to `partial`: the text is unaffected, and
   `partial` is what operators read as "content is missing".
4. **Keep `page` null for HWP.** The format carries no page data; a page
   number may only ever be attached together with a `page_mode`
   (`exact` | `section_approx`) so a reader can tell a real page from a
   section approximation.
5. **Defer legacy `.doc`/`.xls`/`.ppt`.** Measured as not cheaply feasible:
   `olefile` is a container reader with no record-format decoder,
   LibreOffice is not installed, and kordoc does not support them — each
   format would need a from-scratch binary parser comparable to the
   existing hand-rolled HWP5 one.

## Consequences

- PDF tables become citable evidence units for the first time, at a
  measured, operator-tunable ingest cost; existing page evidence is
  unaffected, so no re-extraction is required to keep today's behavior.
- Known limitations to document rather than hide: borderless tables are not
  detected, merged header spans are split, and a table crossing a page break
  becomes one unit per page.
- HWP citations can name a section; page-level precision remains an open gap
  tied to the file format itself.
- Two latent bugs found during the study are fixed: `hwp_broker` read
  kordoc `section`/`sectionNumber` keys that kordoc never emits (dead code),
  and `pageMode` was recorded only at extraction level, leaving any locator
  `page` value ambiguous.
- The HWP section reconstruction calls private methods of a version-pinned
  dependency, isolated behind one helper so an upgrade breaks in exactly one
  place and degrades to `section: null` rather than to wrong data. The HWP5
  (binary `.hwp`) branch is exercised only through a duck-typed fake — no
  real `.hwp` fixture exists in-tree — so its first real-world run should be
  checked for `SECTION_INDEX_UNAVAILABLE` warnings.
- These numbers are the baseline for re-evaluating kordoc or any future
  table/locator candidate; re-run the same probes before switching.

## References

- `src/kip/adapters/parsers/pdf.py`, `hwp_native.py`, `hwp_broker.py`
- `docs/AI_OPERATOR_RUNBOOK.md` (prior HWP parser A/B)
- ADR-042 (OCR routing), ADR-034 (measured-promotion precedent)
- AGENTS.md rules 9 (exact spreadsheet reads) and 12 (shadow extraction)
