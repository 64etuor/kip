# Self-Improving RAG Quality Control Plane Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add reproducible candidate experiments, fail-closed promotion recommendations, and versioned ontology compatibility/migration checks to KIP while preserving explicit human activation.

**Architecture:** Extend the existing file-backed evaluation artifacts and ontology catalog with typed Pydantic boundary models and pure decision functions. Expose them through the shared Typer CLI JSON envelope; do not introduce a second database or let recommendations mutate production state.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, Typer, pytest, existing KIP evaluation and ontology modules.

---

### Task 1: Versioned quality experiment contract

**Files:**
- Create: `src/kip/quality.py`
- Create: `tests/test_quality_contract.py`
- Create: `evaluation/experiments/example.yaml`

**Step 1: Write the failing tests**

Add Given/When/Then tests proving that a complete `kip.quality-experiment.v1` manifest parses, mutable component revisions are rejected, duplicate component kinds are rejected, missing fingerprints are rejected, and the example manifest parses.

**Step 2: Verify red**

Run: `uv run pytest tests/test_quality_contract.py -v`
Expected: FAIL because `kip.quality` does not exist.

**Step 3: Implement the minimum contract**

Create frozen Pydantic models for component kind, component revision, thresholds, coverage requirements, and experiment manifest. Add `load_experiment(Path)` using `yaml.safe_load` at the boundary. Do not add execution or persistence yet.

**Step 4: Verify green**

Run: `uv run pytest tests/test_quality_contract.py -v`
Expected: PASS.

### Task 2: Fail-closed recommendation engine

**Files:**
- Modify: `src/kip/quality.py`
- Modify: `tests/test_quality_contract.py`

**Step 1: Write the failing tests**

Add cases for promotion, category regression, missing required metrics, failed cases, ACL leaks, latency failure, report fingerprint mismatch, and a report variant missing from the experiment.

**Step 2: Verify red**

Run: `uv run pytest tests/test_quality_contract.py -v`
Expected: FAIL because recommendation behavior is absent.

**Step 3: Implement the minimum decision function**

Return a typed recommendation with `promote` or `keep_disabled`, machine-readable gates, and reasons. Reuse the existing evaluation report; do not recompute retrieval metrics or activate a projection.

**Step 4: Verify green**

Run: `uv run pytest tests/test_quality_contract.py -v`
Expected: PASS.

### Task 3: Ontology release diff and migration coverage

**Files:**
- Modify: `src/kip/ontology.py`
- Modify: `tests/test_ontology_contract.py`
- Create: `ontology/migrations/README.md`

**Step 1: Write the failing tests**

Use temporary complete ontology roots to prove additions are compatible; removals, parent changes, domain/range narrowing, and review/risk weakening are breaking; widening and risk tightening require review; breaking changes fail without a matching migration; invalid migration targets fail.

**Step 2: Verify red**

Run: `uv run pytest tests/test_ontology_contract.py -v`
Expected: FAIL because diff and migration APIs are absent.

**Step 3: Implement normalized catalogs and diff**

Extend `OntologyCatalog` with immutable entity and predicate definitions. Add typed `OntologyChange`, `OntologyDiff`, migration operation models, `diff_ontologies`, `load_migration`, and migration coverage validation. Keep the module below the repository size ceiling by moving migration models to `src/kip/ontology_migration.py` if needed.

**Step 4: Verify green**

Run: `uv run pytest tests/test_ontology_contract.py -v`
Expected: PASS and the current ontology still validates.

### Task 4: Versioned CLI surfaces

**Files:**
- Modify: `src/kip/cli.py`
- Modify: `tests/test_cli.py`

**Step 1: Write failing CLI tests**

Test `quality validate-manifest`, `quality recommend`, `ontology validate`, and `ontology diff` through `CliRunner`. Assert parsed envelope fields and exit status, not prose.

**Step 2: Verify red**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL because the command groups are absent.

**Step 3: Implement commands**

Wire the pure quality and ontology functions through the existing `_execute` envelope. Ensure `quality recommend` has no repository/container dependency and cannot activate any projection.

**Step 4: Verify green**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS.

### Task 5: Answer-quality rubric contract

**Files:**
- Modify: `src/kip/evaluation/models.py`
- Create: `src/kip/evaluation/answers.py`
- Create: `tests/test_answer_evaluation.py`
- Modify: `evaluation/schemas/evaluation-report.schema.json`

**Step 1: Write failing tests**

Add reviewed answer fixtures and tests for grounded claim coverage, citation locator correctness, unsupported claims, completeness, and safe refusal. Missing answer evidence must remain unmeasured rather than receiving a passing score.

**Step 2: Verify red**

Run: `uv run pytest tests/test_answer_evaluation.py -v`
Expected: FAIL because answer evaluation is absent.

**Step 3: Implement deterministic rubric inputs**

Model reviewed claims and citations as structured data. Compute only deterministic metrics from those annotations; keep LLM judging as a future candidate adapter, never canonical truth.

**Step 4: Verify green**

Run: `uv run pytest tests/test_answer_evaluation.py tests/test_evaluation_contracts.py -v`
Expected: PASS.

### Task 6: Operator documentation and real canary proof

**Files:**
- Modify: `docs/RAG_EVALUATION.md`
- Modify: `docs/IMPLEMENTATION_STATUS.md`
- Modify: `docs/AI_OPERATOR_RUNBOOK.md`
- Create: `docs/adr/ADR-019-quality-control-plane.md`

**Step 1: Document the lifecycle**

Document candidate registration, shadow execution, recommendation, explicit activation, ontology release diff, migration review, rollback, and how reviewed failures enter the corpus.

**Step 2: Run targeted and complete verification**

Run: `uv run pytest tests/test_quality_contract.py tests/test_ontology_contract.py tests/test_answer_evaluation.py tests/test_cli.py -v`
Expected: PASS.

Run: `./scripts/verify.sh`
Expected: PASS with no new warnings attributable to this change.

**Step 3: Manual QA through the real CLI**

Run manifest validation, recommendation against a current evaluation report, ontology validation, and compatible/breaking diff fixtures through `./scripts/kip`. Confirm every command emits the versioned JSON envelope and no activation state changes.

**Step 4: Run the real canary evaluation**

Run the existing OneDrive-safe evaluation against the reviewed canary dataset without mutating sources. Save its report under `evaluation/reports/`, verify fingerprints, and generate a recommendation. Record measured and unmeasured dimensions separately in `docs/IMPLEMENTATION_STATUS.md`.
