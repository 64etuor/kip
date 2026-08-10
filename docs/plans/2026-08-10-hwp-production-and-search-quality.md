# HWP Production Parser and Search Quality Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make native HWP parsing effective in production and on existing indexes, preserve recoverable activation semantics, and promote only search-quality candidates that beat the native OneDrive baseline.

**Architecture:** Keep parser, analyzer, reranker, and repository integrations behind ports. Add an explicit read-only shadow re-extraction path and an atomic repository activation operation; retain previous extractions for rollback. Evaluate optional search candidates against the same native corpus and enable only a measured winner.

**Tech Stack:** Python 3.12, Pydantic v2 contracts, Typer CLI, PostgreSQL 18, pg_trgm/FTS, pytest, Ruff, mypy, Docker Compose, optional RapidFuzz/Kiwi shadow candidates.

---

### Task 1: Lock the runtime parser contract

**Files:**
- Modify: `tests/test_hwp_parser.py`
- Modify: `config/kip.container.toml`
- Modify: `config/kip.example.toml`
- Modify: `config/kip.toml`

**Step 1: Write the failing contract test**

Load `config/kip.container.toml` through `Settings` and `ParserRegistry`. Assert
that a signature-valid HWP resolves to `HwpParserChain` with a configured native
parser and version `2.0-native-primary`. Assert that the starter Kordoc command is
disabled by default and does not use runtime `npx -y` installation.

**Step 2: Run the test and verify RED**

Run: `.venv/bin/pytest tests/test_hwp_parser.py -q`

Expected: the container contract test fails because no native parser is
configured.

**Step 3: Apply the minimum configuration change**

Put `hwp-hwpx-parser` first in every reference HWP order, enable it with the
bounded unit size, and leave Kordoc disabled with an installed-binary command
template.

**Step 4: Run the test and verify GREEN**

Run: `.venv/bin/pytest tests/test_hwp_parser.py -q`

Expected: all HWP parser tests pass.

**Step 5: Commit**

Commit message: `fix: enable native HWP parsing in containers`

### Task 2: Put text analysis behind a port

**Files:**
- Create: `src/kip/ports/text_analyzer.py`
- Create: `src/kip/adapters/analyzers/__init__.py`
- Create: `src/kip/adapters/analyzers/korean_ngram.py`
- Modify: `src/kip/application/ingestion.py`
- Modify: `src/kip/application/ingestion_files.py`
- Modify: `src/kip/application/ingestion_events.py`
- Modify: `src/kip/application/search.py`
- Modify: `src/kip/application/search_engine.py`
- Modify: `src/kip/container.py`
- Modify: `tests/test_dependency_boundaries.py`
- Test: `tests/test_text_analyzer.py`

**Step 1: Write failing port and parity tests**

Assert the analyzer output remains byte-for-byte compatible for Hangul,
identifiers, punctuation, and normalized whitespace. Add an architecture test
that application modules import `TextAnalyzerPort` and do not import adapter
implementations.

**Step 2: Run the tests and verify RED**

Run: `.venv/bin/pytest tests/test_text_analyzer.py tests/test_dependency_boundaries.py -q`

Expected: imports or the new port are missing.

**Step 3: Move the concrete analyzer**

Define `TextAnalyzerPort.analyze(text: str) -> str`, move the existing n-gram
implementation under `adapters/analyzers`, and inject the port from
`build_container`. Keep normalization as shared deterministic application/domain
logic only if it is parser-independent; otherwise expose it from the analyzer
adapter without importing that adapter from application code.

**Step 4: Run targeted tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_text_analyzer.py tests/test_dependency_boundaries.py tests/test_filesystem_retrieval.py -q`

Expected: analyzer parity and search behavior pass.

**Step 5: Commit**

Commit message: `refactor: isolate text analysis behind a port`

### Task 3: Add recoverable parser re-extraction

**Files:**
- Modify: `src/kip/ports/ingestion.py`
- Modify: `src/kip/adapters/repository/memory/ingestion.py`
- Modify: `src/kip/adapters/repository/postgres/ingestion.py`
- Modify: `src/kip/adapters/repository/postgres/database.py`
- Modify: `src/kip/application/ingestion_files.py`
- Modify: `src/kip/application/ingestion.py`
- Modify: `src/kip/application/runtime.py`
- Modify: `src/kip/cli.py`
- Test: `tests/test_parser_reextraction.py`
- Test: `tests/integration/test_postgres_repository.py`
- Test: `tests/test_cli_surface.py`

**Step 1: Write failing repository contract tests**

Given an active extraction for a current source revision, activate a second
extraction for the same artifact. Assert only the second extraction is searchable,
the first remains stored but inactive, ACL and source hash stay unchanged, and a
rejected candidate leaves the first extraction searchable.

**Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_parser_reextraction.py -q`

Expected: the repository port has no replacement operation.

**Step 3: Implement the atomic repository operation**

Add a typed `replace_extraction` operation to `IngestionStore`. The PostgreSQL
adapter validates the current revision and performs insert, projection swap,
active-flag swap, and audit insert in one transaction. The memory adapter mirrors
observable behavior for contract tests.

**Step 4: Write and verify the CLI RED test**

Assert `parser reextract` reports candidates without mutation and requires
`--activate` for replacement.

Run: `.venv/bin/pytest tests/test_cli_surface.py -q`

Expected: the parser command does not exist.

**Step 5: Implement the application and CLI workflow**

Reuse the filesystem source scanner and packet builder. Parse only HWP/HWPX
records, preserve source hashes and ACL snapshots, reject below-threshold output,
and invoke `replace_extraction` only when `--activate` is present.

**Step 6: Verify GREEN**

Run: `.venv/bin/pytest tests/test_parser_reextraction.py tests/test_cli_surface.py -q`

Expected: shadow and activation behavior pass.

**Step 7: Commit**

Commit message: `feat: add recoverable HWP re-extraction`

### Task 4: Evaluate search-quality candidates

**Files:**
- Modify only if promoted: `pyproject.toml`
- Modify only if promoted: `uv.lock`
- Create only if promoted: `src/kip/adapters/rerankers/rapidfuzz.py`
- Modify only if promoted: `src/kip/application/search_engine.py`
- Modify only if promoted: `src/kip/container.py`
- Test only if promoted: `tests/test_rapidfuzz_reranker.py`
- Record: `docs/adr/ADR-031-hwp-reextraction-and-search-candidates.md`

**Step 1: Run read-only candidate probes**

Compare RapidFuzz, Kiwi morphology, approved ontology alias expansion, and the
operational cost of PostgreSQL BM25/PGroonga adapters. Use native parser output,
the same exact and sparse query set, and no source or canonical database writes.

**Step 2: Apply the promotion gate**

Require Recall@5 not lower than native lexical, MRR improvement above noise,
zero ACL leaks, acceptable P95, a production-compatible license, and a bounded
unit/index footprint. Reject candidates with no measurable improvement.

**Step 3: TDD a winning adapter, or record rejection**

If a candidate passes, first add a failing adapter/selection/E2E test, then the
minimum implementation behind the existing port. If none passes, add no runtime
dependency and record the measured rejection in the ADR and implementation
status.

**Step 4: Commit**

Commit message when promoted: `feat: improve lexical ranking with measured reranking`

Commit message when rejected: `docs: record search candidate promotion decision`

### Task 5: Production documentation and real-corpus activation

**Files:**
- Modify: `docs/CONNECTORS.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/IMPLEMENTATION_STATUS.md`
- Modify: `docs/STARTER_KIT_GUIDE.md`
- Create: `docs/adr/ADR-031-hwp-reextraction-and-search-candidates.md`

**Step 1: Document the operator contract**

Describe native-primary defaults, optional installed Kordoc fallback, shadow
re-extraction, explicit activation, rollback evidence, and the search-candidate
promotion result.

**Step 2: Run full static and test verification**

Run: `./scripts/verify.sh`

Expected: exit 0 with no failed tests or architecture checks.

**Step 3: Run container Manual QA**

Build the production image and run `kip capabilities` with
`KIP_CONFIG=/app/config/kip.container.toml`. Assert the HWP chain reports
`2.0-native-primary` without Node installed.

**Step 4: Run real OneDrive shadow QA**

Capture source hashes, run read-only HWP re-extraction, verify all eligible files
parse, and compare the fingerprinted retrieval report. Confirm source hashes are
unchanged.

**Step 5: Activate the approved native extraction**

Run the explicit activation command only after the shadow report passes. Verify
the active parser distribution and execute search, context, and exact read for
representative OneDrive queries.

**Step 6: Commit**

Commit message: `docs: operationalize HWP parser promotion`
