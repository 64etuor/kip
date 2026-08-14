# ADR-042: Korean OCR enriches candidate pages and presentation images

- **Status:** Accepted
- **Date:** 2026-08-14

## Context

Native PDF text extraction cannot recover scanned pages or damaged Korean font
maps, and PPTX object models expose picture bytes but not text rendered inside
screenshots. Replacing the native parsers with an OCR-first document parser
would discard exact native structure, increase CPU cost, and make every source
dependent on a large model runtime.

Kordoc 4.7.3 exposes local PP-OCRv5 Korean recognition as structured JSON. It
also implements the same PDF quality signals needed here: low text, private-use
glyphs, control characters, and replacement characters. Direct PaddleOCR would
duplicate that process boundary and model lifecycle. Surya OCR 2 is a useful
future precision candidate, but its 650M vision-language runtime is too heavy
for the default CPU indexing path.

## Decision

1. Keep PyMuPDF and `python-pptx` as the native structural parsers. Inject a
   typed optional `OcrPort` only at the adapter boundary.
2. Use an installed Kordoc 4.7.3 executable with PP-OCRv5 Korean. The registry
   requires an exact version probe and rejects `npm`/`npx` runtime commands.
3. Route only PDF pages with fewer than 20 non-whitespace characters, at least
   20% private-use characters, at least 5% control characters, or at least 5%
   replacement characters. Preserve native `pdf_page` units and append
   `pdf_ocr` units with page and pixel bounding boxes.
4. OCR eligible PPTX pictures in one batch per deck. Deduplicate bytes by
   SHA-256, skip small decorations, and enforce count, individual-byte, and
   total-byte limits. Emit one `pptx_ocr` unit per source occurrence with slide,
   slide ID, shape ID, group path, EMU geometry, OCR pixel box, and image hash.
5. Keep OCR units in the same immutable extraction as their native units. The
   composite parser name records the OCR adapter. OCR never creates or mutates
   a source or derived office document.
6. A command failure, timeout, malformed response, or partial OCR warning leaves
   native units available and marks the extraction partial. Image-reference
   blocks and empty text never enter the lexical index.
7. New reference configurations enable PDF/PPTX OCR by default. Bootstrap and
   the production image install exact Kordoc 4.7.3, pre-warm and verify the
   PP-OCRv5 Korean cache, then index with `KORDOC_OFFLINE=1`. Existing local
   configurations are not silently rewritten. The lower-quality HWP Kordoc
   command fallback remains disabled behind the native HWP/HWPX parser.

## Consequences

- Korean text inside scanned PDF pages and PPTX screenshots becomes searchable
  without weakening native document structure or source immutability.
- OCR evidence has reproducible page/shape locators, but recognition remains a
  candidate. Low-confidence warnings require review for material claims.
- Kordoc and model files are default deployment dependencies for new installs;
  KIP downloads them only during bootstrap/image build, never during indexing.
- Audio/video transcription, OLE expansion, legacy `.ppt`, and formula OCR are
  still separate capabilities.

## Evidence

- A synthetic scanned PDF recovered `공정 데이터 관리 시스템` and
  `설비 점검 완료2026-08-14` with page/pixel boxes and unchanged source bytes
  in 0.790 seconds with a warm offline model cache.
- SolarEdge `GEN2 적용 예시.pptx` produced 28 OCR units from seven images in
  4.690 seconds.
- SolarEdge `FAT문제점 및 차트들.pptx` produced 124 OCR units from thirteen
  images in 5.485 seconds. Both decks retained complete
  slide/shape/bounding-box locators and unchanged source size, mtime, and
  SHA-256; low-confidence lines remained explicit warnings rather than silent
  success.
- SolarEdge scanned drawing `라벨추가_KBBZZZZZZ03-01_REV B_Drawing.pdf`
  produced two `pdf_ocr` units and 864 characters with full page coverage and
  unchanged source SHA-256, size, and mtime. A low-confidence warning remained
  explicit and therefore is not a claim of reviewed OCR accuracy.

## References

- https://github.com/chrisryugj/kordoc/tree/v4.7.3
- https://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html
- `docs/PRD.md`
- `docs/TRD.md`
- `docs/DATA_CONTRACTS.md`
- `docs/OPERATIONS.md`
- `docs/SECURITY.md`
