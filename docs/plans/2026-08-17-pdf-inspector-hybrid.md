# PDF Inspector Hybrid Parser Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `pdf-inspector` 1.14.2 the default PDF page/OCR router while retaining selective PyMuPDF exact-table fallback and an explicit PyMuPDF rollback backend.

**Architecture:** Shared PDF OCR and table helpers keep both adapters contract-identical. A new `PdfInspectorParser` emits page Markdown and structured Markdown tables, invoking PyMuPDF only on detected table pages that lack a valid Markdown table. Registry configuration chooses the backend.

**Tech Stack:** Python 3.12+, Pydantic v2 domain contracts, pdf-inspector 1.14.2 PyO3 wheel, PyMuPDF 1.28, Kordoc 4.8, pytest, Ruff, mypy, PostgreSQL.

---

### Task 1: Lock the new adapter behavior with failing tests

**Files:**
- Create: `tests/test_pdf_inspector_parser.py`
- Modify: `tests/test_kordoc_defaults.py`

1. Add Given/When/Then tests for per-page Markdown units and 1-indexed locators.
2. Add a Markdown-table promotion test with row/column/source metadata.
3. Add a bordered-table detection test that requires PyMuPDF fallback.
4. Add decorative-box, OCR-candidate, malformed-PDF, missing-dependency, and
   registry backend-selection tests.
5. Run the focused tests and confirm failures are due to the missing adapter.

### Task 2: Extract shared PDF boundaries

**Files:**
- Create: `src/kip/adapters/parsers/pdf_ocr.py`
- Create: `src/kip/adapters/parsers/pdf_tables.py`
- Modify: `src/kip/adapters/parsers/pdf.py`
- Test: `tests/test_pdf_parser.py`

1. Move OCR reason, OCR-unit construction, warning reconciliation, and context
   types without changing output.
2. Move PyMuPDF table protocols and exact-table construction into the shared
   table module.
3. Run current PDF tests after each move and require byte/metadata parity.

### Task 3: Implement the hybrid PdfInspectorParser

**Files:**
- Create: `src/kip/adapters/parsers/pdf_inspector.py`
- Test: `tests/test_pdf_inspector_parser.py`

1. Dynamically load and exact-version-check `pdf_inspector` at the adapter
   boundary.
2. Parse `extract_pages_markdown` results into `pdf_page` units and metadata.
3. Parse valid Markdown-table blocks into `pdf_table` units.
4. Lazily open PyMuPDF only for detected table pages without Markdown tables.
5. Run shared Kordoc enrichment only for inspector/KIP candidate pages.
6. Convert dependency and parser failures to typed KIP errors.
7. Run focused tests until green, then Ruff and mypy.

### Task 4: Wire dependency and configuration

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `requirements/runtime.txt`
- Modify: `src/kip/adapters/parsers/registry.py`
- Modify: `src/kip/setup/config_payload.py`
- Modify: `config/kip.example.toml`
- Modify: `config/kip.container.toml`
- Test: `tests/test_kordoc_defaults.py`
- Test: `tests/test_setup_writer.py`

1. Add exact `pdf-inspector==1.14.2` to extractor/all profiles and regenerate
   the lock and hashed runtime requirements.
2. Select `pdf_inspector` by default and preserve `pymupdf` rollback.
3. Make guided setup emit the selected default.
4. Run dependency parity, registry, setup, and base-wheel startup tests.

### Task 5: Synchronize canonical documentation

**Files:**
- Create: `docs/adr/ADR-054-pdf-inspector-hybrid-parser.md`
- Modify: `README.md`, `CHANGELOG.md`
- Modify: `docs/PRD.md`, `docs/TRD.md`, `docs/DATA_CONTRACTS.md`
- Modify: `docs/CONNECTORS.md`, `docs/OPERATIONS.md`, `docs/SECURITY.md`
- Modify: `docs/IMPLEMENTATION_STATUS.md`
- Modify: `docs/PRODUCTION_DESIGN_ALIGNMENT.md`
- Modify: `docs/STARTER_KIT_GUIDE.md`, `docs/PRODUCTION_CHECKLIST.md`

Record the backend contract, measured evidence, rollback, resource boundary,
new limitations, and the distinction between Markdown tables and exact-table
fallback. Historical ADR-049 measurements remain historical.

### Task 6: Run isolated end-to-end A/B and release gates

1. Create two temporary local PostgreSQL databases owned by the existing dev
   role and migrate both.
2. Sync the six public PDFs with `pymupdf` and `pdf_inspector` configurations.
3. Run the same public-government lexical/reranked evaluation in both and
   compare recall, MRR, failures, ACL leaks, unit counts, and elapsed sync time.
4. Drop only the two databases created by this task after recording results.
5. Run dependency audits and `./scripts/verify.sh`.
6. Rebuild and verify `dist/kip-starter-kit-3.3.0.zip`; extract elsewhere and
   run bootstrap, doctor, CLI/MCP smoke, and full verification.

