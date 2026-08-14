# Korean OCR Design

## Goal

Add local, Korean-first OCR for scanned PDF pages and raster images embedded in
PPTX files without replacing usable native text or changing source files.

## Decision

Use an exactly pinned Kordoc command adapter backed by PP-OCRv5 Korean. Kordoc
already provides page-level OCR routing, complete precomposed Hangul coverage,
line boxes, and scanned-table reconstruction. Direct PaddleOCR duplicates that
boundary with a heavier Python dependency graph; Surya OCR 2 remains a future
precision escalation because its 650M VLM runtime is disproportionate for the
default CPU path.

Normal sync invokes an installed `kordoc` binary. It never invokes `npx latest`
or downloads code. Setup and operations pin Kordoc 4.7.3 and pre-warm its
verified model cache before OCR is enabled.

## Data flow

```text
PDF -> PyMuPDF fast extraction -> low-text candidate -> Kordoc --ocr JSON
PPTX -> python-pptx structure -> eligible picture batch -> Kordoc image JSON
```

The OCR adapter parses command output once at the trust boundary. OCR blocks
retain text, page number, bounding box, block type, and remaining JSON-safe
metadata. PDF OCR appends `pdf_ocr` candidate units to the immutable extraction
against the original artifact. PPTX OCR appends `pptx_ocr` units while
preserving every native PPTX unit and links each OCR unit to its slide, shape,
image hash, and EMU bounding box.

PPTX images are deduplicated by SHA-256 and bounded by configurable count,
individual byte size, and total byte size. Tiny decorative images are excluded.
One Kordoc process handles the selected image batch so the ONNX model is loaded
once per deck.

## Failure behavior

- An unavailable OCR command leaves native PDF/PPTX extraction usable and adds
  an explicit warning.
- A timeout, malformed JSON, or unsuccessful Kordoc result never erases native
  units. The extraction is `partial` and records the failure category.
- Source bytes are opened read-only and checked before and after real-corpus QA.
- No remote OCR provider, macro execution, external relationship fetch, audio
  transcription, or OLE expansion is introduced.

## Verification

Contract fixtures cover Korean text, bounding boxes, duplicate images, limits,
timeouts, malformed results, multi-file ordering, and fallback behavior. An
end-to-end filesystem sync/search test proves OCR text is retrievable. Manual QA
runs the pinned real Kordoc binary against SolarEdge PPTX images and a scanned
PDF, confirms JSON serialization and source-stat stability, and records unit
counts and observed Korean text.
