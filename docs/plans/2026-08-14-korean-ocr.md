# Korean OCR Implementation Plan

**Goal:** Add bounded local Korean OCR for scanned PDFs and PPTX raster images
using pinned Kordoc PP-OCRv5 output.

**Architecture:** A typed `OcrPort` isolates the external command.
`KordocOcrAdapter` validates structured JSON; small PDF and PPTX wrapper parsers
select OCR candidates while preserving native extraction on failure.

**Tech Stack:** Python 3.13, Pydantic v2, PyMuPDF, python-pptx, Kordoc 4.7.3,
PP-OCRv5 Korean, pytest.

## Task 1: OCR boundary contract and Kordoc adapter

- Create `src/kip/ports/ocr.py` and `src/kip/adapters/ocr/kordoc.py`.
- Test successful block parsing, unsuccessful JSON, timeout, malformed output,
  and multi-file ordering.
- Add frozen OCR request/result models and validate Kordoc JSON at the boundary.

## Task 2: PDF OCR candidate parser

- Add scanned-page tests with exact page/bbox locators and native fallback.
- Inject an optional OCR port into the PDF parser and append separate
  `pdf_ocr` candidate units only for pages whose native text quality requires it.

## Task 3: PPTX picture OCR enrichment

- Test Korean picture text, slide/shape/bbox linkage, duplicate suppression,
  image limits, and native fallback.
- Extract eligible pictures into a temporary batch and append stable
  `pptx_ocr` units without replacing native units.

## Task 4: Configuration and operator workflow

- Add disabled-by-default OCR settings, a pinned command, timeout, and image
  limits.
- Document installation and model pre-warm. Runtime sync must call an installed
  command and must not perform an unpinned network install.

## Task 5: Contracts, architecture, and real QA

- Synchronize PRD, TRD, data contracts, operations, security, status, starter,
  and ADR documentation.
- Verify normal sync/search, lint, type checks, full tests, and portable golden.
- Run Kordoc against representative SolarEdge images and a scanned PDF; record
  OCR units, Korean text, duration, warnings, JSON validity, and unchanged
  source stats.
