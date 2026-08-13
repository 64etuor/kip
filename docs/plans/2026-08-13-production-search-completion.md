# Production Search Completion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Finish the production retrieval, refusal, semantic-runtime, and evaluation gaps while making the approved design match the resulting implementation.

**Architecture:** Keep PostgreSQL canonical and make `SearchRequest` the single retrieval boundary shared by every edge. Add minimal evidence-adequacy checks before answer output, use a required HNSW projection for the PostgreSQL profile, and separate portable CI regression from live private-corpus promotion evidence.

**Tech Stack:** Python 3.12/3.13, Pydantic v2, Typer, FastAPI, FastMCP, PostgreSQL 18, pgvector 0.8, Infinity, pytest, Ruff, mypy.

---

### Task 1: Canonical retrieval contract and edge parity

**Files:**
- Modify: `src/kip/domain/models.py`
- Modify: `src/kip/application/search.py`
- Modify: `src/kip/cli.py`
- Modify: `src/kip/mcp_server.py`
- Modify: `sdk/python/kip_client.py`
- Test: `tests/characterization/test_edge_contract_parity.py`
- Test: `tests/test_sdk_client.py`
- Test: `tests/test_cli_surface.py`

1. Add failing tests proving all four edges accept the same mode, source kind,
   document type, project ID, and candidate-assertion fields.
2. Run the targeted tests and confirm failures identify missing edge fields.
3. Add `mode` to `SearchRequest`; route request mode through
   `RetrievalUseCases.search`; expose the same fields in CLI, MCP, and SDK.
4. Run the targeted tests and contract generation check.

### Task 2: ACL scope replacement semantics

**Files:**
- Modify: `src/kip/application/operations.py`
- Modify: `src/kip/cli.py`
- Test: `tests/test_cli_runtime_boundaries.py`
- Test: `tests/test_acl_freshness.py`

1. Add a failing CLI test where ambient `KIP_ACL_SCOPES` is present and an
   explicit `--acl-scope` must replace it.
2. Add a failing application test proving `acl_scopes=[]` stays empty.
3. Implement explicit-versus-ambient precedence and `None`-only defaulting.
4. Run the ACL and edge tests.

### Task 3: Evidence-bounded refusal and XLSX JSON safety

**Files:**
- Modify: `src/kip/application/answers.py`
- Modify: `src/kip/domain/models.py`
- Modify: `src/kip/adapters/parsers/xlsx_read.py` only if the boundary test fails
- Test: `tests/test_answers.py`
- Test: `tests/test_api.py`
- Test: `tests/test_xlsx_two_stage.py`

1. Add failing tests for an absent explicit identifier, a numeric question with
   no numeric evidence, and a generic multi-document question requiring
   clarification.
2. Add a JSON-envelope test with date, datetime, time, and formula cells.
3. Implement only the minimum typed refusal rules needed by the failing tests;
   normalize workbook scalar values only if Pydantic JSON mode does not already
   preserve them.
4. Run answer, API, XLSX, and generated-answer tests.

### Task 4: Semantic readiness and HNSW runtime

**Files:**
- Create: `migrations/0018_embeddings_1024_hnsw.sql`
- Modify: `src/kip/application/operations.py`
- Modify: `src/kip/adapters/repository/postgres/database.py`
- Modify: `src/kip/domain/models.py`
- Modify: `config/kip.example.toml`
- Test: `tests/integration/test_postgres_semantic_projection.py`
- Test: `tests/test_semantic_repository_contract.py`

1. Add failing tests for HNSW migration presence, configured `ef_search`, and
   capability false/true transitions across missing, shadow, incomplete, and
   active compatible spaces.
2. Add append-only migration 0018 and set bounded `hnsw.ef_search` on vector
   queries.
3. Define semantic capability from active compatible projection readiness.
4. Run semantic unit and PostgreSQL integration tests.

### Task 5: 100+ case regression and live promotion gates

**Files:**
- Modify: `scripts/golden_gate.py`
- Create: `scripts/portable_acceptance_gate.py`
- Create: `evaluation/golden/production-regression.yaml`
- Create: `evaluation/corpus/production-regression/`
- Modify: `.github/workflows/ci.yml`
- Modify: `scripts/verify.sh`
- Test: `tests/test_evaluation_runner.py`
- Test: `tests/test_public_corpus.py`

1. Add failing tests requiring at least 100 immutable reviewed regression cases
   and making a required private gate fail rather than silently skip.
2. Add a deterministic 100+ case portable corpus covering exact, numeric,
   paraphrase, code-switch, answerless, unknown-ID, stale, and ACL scenarios.
3. Run the portable gate in CI; keep the private gate separately configurable
   and fail closed when production explicitly requires it.
4. Run both gates locally against their matching corpus.

### Task 6: Live sidecar and semantic evaluation

**Files:**
- Modify only if measured failures require it: `scripts/semantic-server.sh`,
  `src/kip/adapters/embeddings/http.py`, or semantic operations modules
- Produce: `evaluation/reports/semantic-qwen3-20260813/`

1. Start Infinity in a foreground-managed session and verify `/models`.
2. Apply migration 0018 and verify projection completeness.
3. Warm the service, then measure cold and warm endpoint latency separately.
4. Run lexical/vector/hybrid/reranked evaluation on the reviewed private set.
5. Keep semantic disabled unless every activation gate passes; record the
   measured decision either way.

### Task 7: Documentation and final verification

**Files:**
- Modify: `docs/PRD.md`, `docs/TRD.md`, `docs/DATA_CONTRACTS.md`
- Modify: `docs/OPERATIONS.md`, `docs/SECURITY.md`
- Modify: `docs/IMPLEMENTATION_STATUS.md`
- Modify: `docs/PRODUCTION_DESIGN_ALIGNMENT.md`
- Modify: `docs/PRODUCTION_CHECKLIST.md`, `docs/RAG_EVALUATION.md`, `README.md`

1. Update current contracts, capability semantics, pgvector/HNSW requirement,
   refusal behavior, evaluation evidence, and remaining environment gates.
2. Run generated-contract, structure, Ruff, mypy, no-excuse, link, and diff
   checks.
3. Run `scripts/verify.sh` once with the real PostgreSQL environment.
4. Manually exercise CLI search/context/answer/XLSX/capabilities, REST search
   and answer, MCP tool registration, SDK payload construction, and live
   semantic retrieval.
