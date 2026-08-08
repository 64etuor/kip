# Evidence-bounded RAG Reinforcement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a safe, cited answer surface and a reproducible black-box/private evaluation path without weakening KIP evidence, ACL, or candidate-review invariants.

**Architecture:** Add typed answer contracts to the domain, implement deterministic exact-evidence assembly in the application service, and expose it through CLI/API/MCP. Keep relation generation explicit and idempotent. Version scenario cases as evaluation data and verify with no-context agents plus automated tests.

**Tech Stack:** Python 3.12+, Pydantic v2, Typer, FastAPI, pytest, PostgreSQL/memory repository.

---

### Task 1: Answer contract

**Files:** `src/kip/domain/models.py`, `tests/test_answer_service.py`

1. Write failing tests for cited fresh evidence, no-evidence refusal, stale-evidence refusal, and XLSX numeric refusal.
2. Run the focused test and confirm the missing contract/service failure.
3. Add frozen Pydantic request, citation, and response models.
4. Run the focused tests.

### Task 2: Application answer service

**Files:** `src/kip/application/answers.py`, `src/kip/application/services.py`, `tests/test_answer_service.py`

1. Implement exact-read assembly with bounded passages.
2. Exclude stale/unreadable evidence and return structured refusal reason.
3. Require exact XLSX range reading for numeric/table claims.
4. Prove no sync, projection, or candidate mutation occurs.

### Task 3: Edge parity

**Files:** `src/kip/cli.py`, `src/kip/api.py`, `src/kip/mcp_server.py`, `tests/test_cli_surface.py`, `tests/test_api.py`

1. Add failing CLI/API contract tests.
2. Expose the same service as `kip answer`, `POST /v1/answer`, and MCP `kip_answer`.
3. Regenerate and check OpenAPI/contracts.

### Task 4: Private scenario dataset

**Files:** `evaluation/golden/private-starter.yaml`, `evaluation/README.md`, `docs/AI_OPERATOR_RUNBOOK.md`

1. Add redacted/sample cases for exact, paraphrase, XLSX, stale/refusal, graph empty-state, and ACL denial.
2. Document that private production cases must replace sample expectations and remain outside public git when sensitive.
3. Add a black-box run record containing prompt, commands discovered, evidence used, outcome, and defect class.

### Task 5: Verification

1. Run targeted pytest, ruff, mypy, contract generation, and `./scripts/verify.sh`.
2. Run the real sample CLI `answer` and XLSX flow.
3. Re-dispatch no-context agents with the same prompts and compare outcomes.
