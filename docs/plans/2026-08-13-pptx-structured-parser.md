# Structured PPTX Parser Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add evidence-preserving PPTX ingestion and preserve Kordoc structured blocks instead of flattening or dropping them.

**Architecture:** Use `python-pptx` for the PPTX object model and a bounded standard-library OOXML scan for hidden slides, comments, diagram text, and relationship metadata. Emit one `ContentUnit` per meaningful slide shape or slide notes block with exact slide/shape locators. Keep Kordoc as an optional command adapter, but translate its IR tables, nested blocks, images, and structured warnings at the adapter boundary.

**Tech Stack:** Python 3.12+, python-pptx, zipfile/ElementTree, Pydantic domain models, pytest, ruff, mypy.

---

### Task 1: Lock the Kordoc adapter contract

**Files:**
- Modify: `tests/test_hwp_parser.py`
- Modify: `src/kip/adapters/parsers/hwp_broker.py`

1. Add a failing broker test whose command payload contains a Kordoc `table` block and structured warning.
2. Run the targeted test and confirm the table is dropped by the current `rows`-only fallback.
3. Add typed Kordoc block rendering that preserves table cells, nested blocks, spans, footnotes, image metadata, and warning code/page/message.
4. Run the targeted test and existing HWP parser tests.

### Task 2: Define PPTX evidence behavior

**Files:**
- Create: `tests/test_pptx_parser.py`
- Create: `src/kip/adapters/parsers/pptx.py`

1. Add failing tests for text, exact slide/shape locators, geometry, stable shape order, tables, charts, images, notes, hidden slides, comments, and malformed packages.
2. Run the tests and confirm failure because `PptxParser` is absent.
3. Implement the smallest parser that emits JSON-safe `ContentUnit` metadata and structured warnings while never executing macros or resolving external links.
4. Run the targeted tests until green.

### Task 3: Register and package the parser

**Files:**
- Modify: `tests/test_parser_registry.py`
- Modify: `src/kip/adapters/parsers/registry.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `config/kip.example.toml`
- Modify: `config/kip.container.toml`

1. Add a failing registry test for `.pptx` and `.pptm` selection.
2. Register the parser and add `python-pptx>=1.0.2,<2` to extractor/all extras.
3. Add PPTX extensions to reference source profiles without changing existing operator overrides.
4. Regenerate the lockfile and run registry/dependency tests.

### Task 4: Synchronize public design and operations documentation

**Files:**
- Modify: `docs/PRD.md`
- Modify: `docs/TRD.md`
- Modify: `docs/DATA_CONTRACTS.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/SECURITY.md`
- Modify: `docs/IMPLEMENTATION_STATUS.md`
- Modify: `docs/PRODUCTION_DESIGN_ALIGNMENT.md`
- Create: `docs/adr/ADR-041-structured-pptx-extraction.md`

1. Document the locator contract, shape-order semantics, unsupported/partial elements, dependency, resource limits, and shadow activation policy.
2. Record the architecture decision and measured test evidence without claiming corpus readiness before real-file QA.

### Task 5: Verify through the real KIP surface

1. Run targeted PPTX, HWP broker, registry, contract, lint, and type checks.
2. Run `./scripts/verify.sh`; distinguish baseline failures from regressions.
3. Parse representative real SolarEdge PPTX files through `./scripts/kip sync run` or an equivalent real parser driver without mutating source files.
4. Confirm slide counts, text, notes/tables/charts/images where present, exact locators, warnings, unchanged source hashes, and bounded processing.
