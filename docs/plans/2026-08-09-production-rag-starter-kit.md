# Production RAG Starter Kit Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver a production-grade, privately distributable, headless KIP RAG system with agent-guided setup, trusted identity and ACL freshness, evidence-bounded generation, reviewed ontology RAG, observability, evaluation, and recovery evidence.

**Architecture:** Preserve public IDs and versioned edge contracts while replacing the monolithic application and repository seams with focused use cases and capability ports. PostgreSQL remains canonical; all generated knowledge and projections remain evidence-backed, ACL-prefiltered, versioned, and explicitly promoted.

**Tech Stack:** Python 3.12/3.13, Pydantic v2, Typer, FastAPI, PostgreSQL 18/psycopg 3, httpx, PyYAML, OpenTelemetry-compatible telemetry, pytest, Ruff, mypy, Docker Compose.

**Execution policy:** Work in the current checkout as explicitly requested. Stage only task-owned paths, preserve unrelated untracked files, run a focused red/green cycle for every behavior, run `./scripts/verify.sh` before each commit, and use small conventional commits.

---

## Milestone 1: Application and storage boundaries

### Task 1: Pin external behavior before structural refactoring

**Files:**
- Create: `tests/characterization/test_edge_contract_parity.py`
- Create: `tests/characterization/test_application_dependency_rules.py`
- Modify: `scripts/verify_project.py`

**Step 1: Write the failing boundary test**

Add an AST-based test that rejects imports from `kip.adapters` inside
`src/kip/application` and verifies that CLI and REST expose the same envelope
schema for capabilities, search, answer, assertion explanation, and graph
requests.

```python
def test_application_does_not_import_concrete_adapters() -> None:
    violations = application_adapter_imports(ROOT / "src/kip/application")
    assert violations == []
```

**Step 2: Verify red**

Run:

```bash
.venv/bin/pytest tests/characterization/test_application_dependency_rules.py -q
```

Expected: FAIL listing the concrete connector and parser imports in
`src/kip/application/services.py`.

**Step 3: Add the rule to project verification without changing production code**

Share the AST helper with `scripts/verify_project.py`; keep the test red until
Task 2 removes the imports.

**Step 4: Record the edge baseline**

Run the parity characterization test and save only stable semantic fields, never
full prose snapshots.

**Step 5: Commit with Task 2 after green**

Do not commit a deliberately failing branch.

### Task 2: Split capability ports and application use cases

**Files:**
- Create: `src/kip/ports/ingestion.py`
- Create: `src/kip/ports/retrieval.py`
- Create: `src/kip/ports/evidence.py`
- Create: `src/kip/ports/knowledge.py`
- Create: `src/kip/ports/jobs.py`
- Create: `src/kip/ports/operations.py`
- Create: `src/kip/application/ingestion.py`
- Create: `src/kip/application/search.py`
- Create: `src/kip/application/evidence.py`
- Create: `src/kip/application/knowledge.py`
- Create: `src/kip/application/operations.py`
- Create: `src/kip/application/runtime.py`
- Modify: `src/kip/ports/repository.py`
- Modify: `src/kip/application/services.py`
- Modify: `src/kip/container.py`
- Modify: `src/kip/cli.py`
- Modify: `src/kip/api.py`
- Modify: `src/kip/mcp_server.py`
- Modify: `src/kip/worker.py`
- Test: `tests/characterization/test_application_dependency_rules.py`
- Test: `tests/characterization/test_edge_contract_parity.py`
- Test: existing CLI, API, worker, retrieval, and repository suites

**Step 1: Define narrow capability protocols**

Use protocols such as:

```python
class RetrievalStore(Protocol):
    def search(
        self,
        context: RequestContext,
        request: SearchRequest,
        lexemes: str,
    ) -> list[SearchHit]: ...

    def get_content_units(
        self,
        context: RequestContext,
        unit_ids: Sequence[str],
    ) -> list[ContentUnit]: ...
```

`RepositoryPort` may temporarily compose these protocols for adapter
compatibility, but no new use case accepts the aggregate protocol.

**Step 2: Extract use cases without changing behavior**

Move sync and ingest, search and context, exact evidence reads, assertion review,
and projection operations into focused classes. Each class receives only the
ports it consumes.

**Step 3: Make composition explicit**

Replace `Container.service` with an immutable application bundle:

```python
@dataclass(frozen=True, slots=True)
class Application:
    ingestion: IngestionUseCases
    retrieval: RetrievalUseCases
    evidence: EvidenceUseCases
    knowledge: KnowledgeUseCases
    operations: OperationsUseCases
```

Update every edge adapter to call this bundle.

**Step 4: Remove concrete adapter imports from application code**

Connector selection, parser selection, XLSX implementation selection, and
vendor construction move to `container.py` or adapter-owned factories.

**Step 5: Verify green**

Run:

```bash
.venv/bin/pytest tests/characterization tests/test_api.py tests/test_cli_surface.py \
  tests/test_worker.py tests/test_hybrid_retrieval.py -q
./scripts/verify.sh
```

Expected: all pass, including the previously failing dependency rule.

**Step 6: Commit**

```bash
git add src/kip/ports src/kip/application src/kip/container.py src/kip/cli.py \
  src/kip/api.py src/kip/mcp_server.py src/kip/worker.py tests/characterization \
  scripts/verify_project.py
git commit -m "refactor: split application capability boundaries"
```

### Task 3: Split PostgreSQL and memory stores by capability

**Files:**
- Create: `src/kip/adapters/repository/postgres/__init__.py`
- Create: `src/kip/adapters/repository/postgres/database.py`
- Create: `src/kip/adapters/repository/postgres/ingestion.py`
- Create: `src/kip/adapters/repository/postgres/retrieval.py`
- Create: `src/kip/adapters/repository/postgres/evidence.py`
- Create: `src/kip/adapters/repository/postgres/knowledge.py`
- Create: `src/kip/adapters/repository/postgres/jobs.py`
- Create: `src/kip/adapters/repository/postgres/operations.py`
- Create: `src/kip/adapters/repository/memory/__init__.py`
- Create: `src/kip/adapters/repository/memory/stores.py`
- Delete after extraction: `src/kip/adapters/repository/postgres.py`
- Delete after extraction: `src/kip/adapters/repository/memory.py`
- Modify: `src/kip/adapters/repository/__init__.py`
- Modify: `src/kip/container.py`
- Test: `tests/integration/test_postgres_repository.py`
- Test: `tests/test_semantic_repository_contract.py`
- Test: `tests/test_vocabulary_and_assertions.py`

**Step 1: Add contract tests for each capability**

Run the same contract against memory and PostgreSQL stores for ingestion,
retrieval, evidence, jobs, and knowledge. Use a real PostgreSQL service for the
integration implementation.

**Step 2: Verify the new contracts fail before adapters are split**

Expected: imports or constructors are missing, not existing behavior failures.

**Step 3: Extract a shared PostgreSQL database/session object**

Connection setup, transaction boundaries, statement timeout, RLS context, and
workspace/principal establishment live in `database.py`. Capability adapters
receive that object; they do not inherit a giant mixin hierarchy.

**Step 4: Move methods capability by capability**

After moving each group, run only its contract suite. Delete the old method only
after both memory and PostgreSQL implementations pass. Replace each old module
with a package `__init__.py` that exports the same public adapter class names, so
imports switch atomically and a module and package never coexist under one name.

**Step 5: Verify the full baseline**

```bash
./scripts/verify.sh
```

**Step 6: Commit**

```bash
git add src/kip/adapters/repository src/kip/container.py tests
git commit -m "refactor: compose repositories from capability stores"
```

## Milestone 2: Agent-guided safe setup

### Task 4: Add versioned setup contracts and planning state machine

**Files:**
- Create: `src/kip/setup/__init__.py`
- Create: `src/kip/setup/models.py`
- Create: `src/kip/setup/planner.py`
- Create: `src/kip/setup/inventory.py`
- Create: `src/kip/setup/writer.py`
- Create: `src/kip/setup/service.py`
- Create: `tests/test_setup_models.py`
- Create: `tests/test_setup_planner.py`
- Create: `tests/test_setup_writer.py`
- Modify: `pyproject.toml`
- Modify: `scripts/generate_contracts.py`

**Step 1: Write failing tests for the setup dialogue contract**

Cover missing-question ordering, resume behavior, excessive root rejection,
source preview, secret references, personal/company separation, model egress,
ontology reviewer ownership, plan fingerprinting, atomic apply, and redaction.

```python
def test_setup_inspection_asks_only_the_first_missing_decision() -> None:
    inspection = inspect_setup(SetupAnswers())
    assert [question.id for question in inspection.questions] == ["workspace"]
```

**Step 2: Verify red**

```bash
.venv/bin/pytest tests/test_setup_models.py tests/test_setup_planner.py \
  tests/test_setup_writer.py -q
```

**Step 3: Implement strict models**

Add `kip.setup-answers.v1`, `kip.setup-inspection.v1`, `kip.setup-plan.v1`, and
`kip.setup-receipt.v1`. Store only secret references. Reject `/`, the resolved
home directory, and the repository root as collection roots.

**Step 4: Implement read-only inventory and pure planning**

Inventory returns canonical path, file count, byte count, extension counts, and
excluded counts. It never opens document content or follows symlinks unless the
approved source policy allows it.

**Step 5: Implement atomic generated configuration**

Use `tomli-w` for TOML and `yaml.safe_dump` for Compose overrides. Write to a
temporary sibling, fsync, and replace. Preserve one previous generated version.

**Step 6: Generate and validate JSON schemas**

```bash
.venv/bin/python scripts/generate_contracts.py
.venv/bin/python scripts/generate_contracts.py --check
```

**Step 7: Verify and commit**

```bash
./scripts/verify.sh
git add src/kip/setup tests/test_setup_*.py pyproject.toml uv.lock \
  scripts/generate_contracts.py contracts
git commit -m "feat: add deterministic setup planning"
```

### Task 5: Expose setup through CLI and a portable agent Skill

**Files:**
- Create: `src/kip/setup_cli.py`
- Create: `skills/kip-setup/SKILL.md`
- Create: `skills/kip-setup/agents/openai.yaml`
- Create: `skills/kip-setup/references/questions.md`
- Create: `.claude/skills/kip-setup/SKILL.md`
- Create: `.claude/skills/kip-setup/agents/openai.yaml`
- Create: `.claude/skills/kip-setup/references/questions.md`
- Create: `tests/test_setup_cli.py`
- Create: `tests/test_setup_skill.py`
- Modify: `src/kip/cli.py`
- Modify: `scripts/verify_project.py`
- Modify: `docs/QUICKSTART.md`
- Modify: `docs/STARTER_KIT_GUIDE.md`

**Step 1: Write CLI E2E tests first**

Exercise `setup inspect`, `setup answer`, `setup preview`, `setup plan`, `setup
apply`, and `setup verify` in an isolated filesystem. Prove malformed and stale
plans fail before writes.

**Step 2: Verify red**

```bash
.venv/bin/pytest tests/test_setup_cli.py tests/test_setup_skill.py -q
```

**Step 3: Add a setup CLI that works before normal runtime construction**

The root callback must not require a production config, database, parser, or
model client for `kip setup`. Setup commands emit the standard versioned JSON
envelope.

**Step 4: Add the portable Skill**

The Skill runs `inspect`, asks exactly one returned question, records the answer
through the CLI, previews sources, shows the plan, requests user approval, then
runs apply and verify. It never edits raw config or secrets itself.

**Step 5: Verify Skill tree parity and real CLI flow**

```bash
./scripts/kip setup inspect --state /tmp/kip-setup-state.json
./scripts/verify.sh
```

**Step 6: Commit**

```bash
git add src/kip/cli.py src/kip/setup_cli.py skills/kip-setup \
  .claude/skills/kip-setup tests/test_setup_* scripts/verify_project.py \
  docs/QUICKSTART.md docs/STARTER_KIT_GUIDE.md
git commit -m "feat: guide agents through safe KIP setup"
```

## Milestone 3: Trusted identity, ACL freshness, and egress policy

### Task 6: Add trusted identity resolution and ACL snapshots

**Files:**
- Create: `migrations/0007_identity_acl_snapshots.sql`
- Create: `src/kip/domain/identity.py`
- Create: `src/kip/ports/identity.py`
- Create: `src/kip/adapters/identity/__init__.py`
- Create: `src/kip/adapters/identity/api_key.py`
- Create: `src/kip/adapters/identity/jwt.py`
- Create: `tests/test_identity_adapters.py`
- Create: `tests/test_acl_freshness.py`
- Modify: `src/kip/api.py`
- Modify: `src/kip/settings.py`
- Modify: `src/kip/container.py`
- Modify: `src/kip/adapters/connectors/*.py`
- Modify: `src/kip/adapters/repository/postgres/ingestion.py`
- Modify: `src/kip/adapters/repository/postgres/retrieval.py`
- Modify: `src/kip/adapters/repository/postgres/knowledge.py`
- Modify: `config/kip.example.toml`
- Modify: `.env.example`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `deploy/sql/roles.sql.template`
- Test: `tests/integration/test_postgres_repository.py`

**Step 1: Write failing trust-boundary tests**

Prove arbitrary identity headers are rejected outside development, signed or JWT
claims map to a typed principal, stale dynamic ACL snapshots are excluded before
retrieval, and graph paths do not reveal inaccessible node existence.

**Step 2: Verify red**

```bash
.venv/bin/pytest tests/test_identity_adapters.py tests/test_acl_freshness.py -q
```

**Step 3: Add append-only ACL schema**

Store source ACL snapshot ID, version, captured time, expiry, provider, and scope
mapping. Add indexes for active and stale lookups. Preserve existing static
filesystem scopes as non-expiring configuration-owned snapshots.

**Step 4: Add identity adapters**

Support local operator/API-key mode for development and JWT issuer, audience,
JWKS, principal claim, and group claim for production. Cache JWKS with an
explicit deadline; failed verification denies access.

Add the pinned JWT/crypto dependency as an optional identity extra and test the
minimal install without it. Production JWT mode fails fast when the extra is
missing.

**Step 5: Apply ACL freshness inside every query**

Lexical, vector, evidence, assertion, and graph SQL must exclude stale dynamic
snapshots before returning rows. Do not filter results in application memory.

**Step 6: Run PostgreSQL integration and RLS role checks**

```bash
KIP_TEST_POSTGRES_URL="$KIP_DATABASE_URL" \
  .venv/bin/pytest tests/integration/test_postgres_repository.py \
  tests/test_acl_freshness.py -q
./scripts/verify.sh
```

**Step 7: Commit**

```bash
git add migrations/0007_identity_acl_snapshots.sql src/kip/domain/identity.py \
  src/kip/ports/identity.py src/kip/adapters/identity src/kip/api.py \
  src/kip/settings.py src/kip/container.py src/kip/adapters/connectors \
  src/kip/adapters/repository config/kip.example.toml .env.example \
  deploy/sql/roles.sql.template pyproject.toml uv.lock tests
git commit -m "feat: enforce trusted identity and fresh ACLs"
```

### Task 7: Enforce typed model egress policy

**Files:**
- Create: `migrations/0008_data_classification.sql`
- Create: `src/kip/domain/egress.py`
- Create: `src/kip/application/egress.py`
- Create: `tests/test_egress_policy.py`
- Modify: `src/kip/domain/models.py`
- Modify: `src/kip/ports/ingestion.py`
- Modify: `src/kip/ports/retrieval.py`
- Modify: `src/kip/adapters/connectors/*.py`
- Modify: `src/kip/adapters/repository/postgres/ingestion.py`
- Modify: `src/kip/adapters/repository/postgres/retrieval.py`
- Modify: `src/kip/adapters/repository/memory/stores.py`
- Modify: `src/kip/settings.py`
- Modify: `config/kip.example.toml`
- Modify: `docs/SECURITY.md`

**Step 1: Write failing policy matrix tests**

Cover local, internal, confidential, restricted, and personal data against local,
OpenAI, and Anthropic destinations. Missing provider, classification, retention,
or secret reference must deny egress.

**Step 2: Implement parse-at-boundary models**

`EgressDecision` returns allowed evidence IDs and a machine-readable denial
reason. It never silently drops disallowed evidence while claiming a complete
answer.

Store a typed data classification on source objects and content units so the
policy is enforced from canonical evidence, not from a caller-supplied label.
Ingestion derives it from the approved source configuration and preserves it in
every projection.

**Step 3: Verify and commit**

```bash
.venv/bin/pytest tests/test_egress_policy.py -q
./scripts/verify.sh
git add migrations/0008_data_classification.sql src/kip/domain/egress.py \
  src/kip/application/egress.py src/kip/domain/models.py src/kip/ports \
  src/kip/adapters/connectors src/kip/adapters/repository \
  tests/test_egress_policy.py src/kip/settings.py config/kip.example.toml \
  docs/SECURITY.md
git commit -m "feat: gate model egress by data policy"
```

## Milestone 4: Evidence-bounded generation

### Task 8: Add provider-neutral structured generation

**Files:**
- Create: `src/kip/domain/generation.py`
- Create: `src/kip/ports/generation.py`
- Create: `src/kip/adapters/generators/__init__.py`
- Create: `src/kip/adapters/generators/openai_compatible.py`
- Create: `src/kip/adapters/generators/anthropic.py`
- Create: `tests/test_generation_contract.py`
- Create: `tests/test_generator_http_adapters.py`
- Modify: `src/kip/settings.py`
- Modify: `src/kip/container.py`
- Modify: `config/kip.example.toml`
- Modify: `.env.example`

**Step 1: Write the generation contract tests**

Use wire-level HTTP fixtures. Require a typed result:

```python
class GeneratedClaim(StrictModel):
    text: str
    evidence_ids: tuple[str, ...]
    certainty: Literal["supported", "uncertain"]

class GenerationResult(StrictModel):
    claims: tuple[GeneratedClaim, ...]
    model: ModelRevision
    usage: GenerationUsage
```

Test timeout, non-JSON response, unknown evidence IDs, cardinality, token usage,
provider errors, and cancellation. Do not assert natural-language prompt prose.

Before writing an adapter, re-read the current official OpenAI Responses and
Anthropic Messages structured-output documentation and pin the request and
response fields used by the wire contract. Do not implement from remembered API
shapes.

**Step 2: Verify red**

```bash
.venv/bin/pytest tests/test_generation_contract.py \
  tests/test_generator_http_adapters.py -q
```

**Step 3: Implement HTTP adapters with production defaults**

Use explicit connect/read/write/pool timeouts, bounded response bytes, provider
request IDs, structured output schemas, no automatic SDK retries, and redacted
errors. Local OpenAI-compatible endpoints and approved remote providers share the
same port.

**Step 4: Verify and commit**

```bash
./scripts/verify.sh
git add src/kip/domain/generation.py src/kip/ports/generation.py \
  src/kip/adapters/generators tests/test_generation_contract.py \
  tests/test_generator_http_adapters.py src/kip/settings.py src/kip/container.py \
  config/kip.example.toml .env.example
git commit -m "feat: add structured generation adapters"
```

### Task 9: Replace extractive-only answers with verified claim assembly

**Files:**
- Create: `src/kip/application/answering.py`
- Create: `src/kip/application/citations.py`
- Create: `tests/test_generated_answers.py`
- Modify: `src/kip/application/answers.py`
- Modify: `src/kip/domain/models.py`
- Modify: `src/kip/application/runtime.py`
- Modify: `src/kip/cli.py`
- Modify: `src/kip/api.py`
- Modify: `src/kip/mcp_server.py`
- Modify: `sdk/python/kip_client.py`
- Modify: `contracts/*answer*`
- Test: `tests/test_answers.py`
- Test: `tests/test_api.py`
- Test: `tests/test_cli_surface.py`

**Step 1: Write failing answer E2E tests**

Prove every material claim cites an admitted exact evidence ID, stale evidence is
excluded, unknown citations fail, egress denial is explicit, XLSX numeric intent
requires a live range, generator failure follows configured extractive/refusal
policy, and CLI/REST/MCP semantics match.

**Step 2: Verify red**

```bash
.venv/bin/pytest tests/test_generated_answers.py tests/test_api.py \
  tests/test_cli_surface.py -q
```

**Step 3: Implement answer orchestration**

Search, select, reopen, freshness-check, apply egress policy, generate structured
claims, validate evidence references, and assemble citations in one use case.
Generation never receives raw search hits.

**Step 4: Preserve deterministic fallback semantics**

Disabled generation retains evidence-bounded extractive responses. Unavailable
generation returns a typed refusal unless the configured fallback is explicitly
allowed.

**Step 5: Regenerate contracts, verify, and commit**

```bash
.venv/bin/python scripts/generate_contracts.py
./scripts/verify.sh
git add src/kip/application/answering.py src/kip/application/citations.py \
  src/kip/application/answers.py src/kip/domain/models.py src/kip/application/runtime.py \
  src/kip/cli.py src/kip/api.py src/kip/mcp_server.py sdk/python/kip_client.py \
  contracts tests/test_generated_answers.py tests/test_answers.py tests/test_api.py \
  tests/test_cli_surface.py
git commit -m "feat: verify generated claims against exact evidence"
```

## Milestone 5: Reviewed ontology RAG

### Task 10: Add typed entities, evidence, and idempotent relation candidates

**Files:**
- Create: `migrations/0009_ontology_rag.sql`
- Create: `src/kip/domain/knowledge.py`
- Create: `src/kip/ports/relation_miner.py`
- Create: `src/kip/application/ontology_rag.py`
- Create: `tests/test_ontology_rag_domain.py`
- Create: `tests/test_relation_candidate_idempotency.py`
- Modify: `src/kip/ports/knowledge.py`
- Modify: `src/kip/adapters/repository/memory/stores.py`
- Modify: `src/kip/adapters/repository/postgres/knowledge.py`
- Modify: `src/kip/ontology.py`
- Modify: `src/kip/ids.py`
- Test: `tests/test_ontology_contract.py`
- Test: `tests/integration/test_postgres_repository.py`

**Step 1: Write failing domain and integration tests**

Cover entity creation and aliases, ontology type validation, candidate evidence,
domain/range mismatch, stable fingerprint deduplication, temporal bounds,
contradictions, evidence ACL intersection, and review risk.

**Step 2: Verify red**

```bash
.venv/bin/pytest tests/test_ontology_rag_domain.py \
  tests/test_relation_candidate_idempotency.py -q
```

**Step 3: Add append-only ontology RAG schema**

Add normalized entity candidates and identifiers, assertion candidate
fingerprints, typed candidate evidence, valid intervals, derivation metadata,
and uniqueness constraints. Backfill existing candidate JSON evidence into the
normalized table without rewriting approved assertions.

**Step 4: Implement typed candidate creation**

Fingerprint ontology version, source revision, locator, normalized subject,
predicate, object, and miner revision. Validate entity types against ontology
domain and range before persistence.

**Step 5: Verify memory/PostgreSQL parity and commit**

```bash
./scripts/verify.sh
git add migrations/0009_ontology_rag.sql src/kip/domain/knowledge.py \
  src/kip/ports/knowledge.py src/kip/ports/relation_miner.py \
  src/kip/application/ontology_rag.py src/kip/adapters/repository \
  src/kip/ontology.py src/kip/ids.py tests
git commit -m "feat: add typed ontology relation candidates"
```

### Task 11: Add generator-backed relation mining and review surfaces

**Files:**
- Create: `src/kip/adapters/relation_miners/__init__.py`
- Create: `src/kip/adapters/relation_miners/generator.py`
- Create: `tests/test_relation_miner_contract.py`
- Create: `tests/test_ontology_rag_cli.py`
- Modify: `src/kip/worker.py`
- Modify: `src/kip/cli.py`
- Modify: `src/kip/api.py`
- Modify: `src/kip/mcp_server.py`
- Modify: `src/kip/container.py`
- Modify: `src/kip/domain/models.py`
- Modify: `sdk/python/kip_client.py`
- Modify: `contracts/`

**Step 1: Write failing structured-miner tests**

The miner receives exact evidence plus the active ontology and returns typed
entity and relation proposals. Test unknown predicates, invalid types, missing
evidence, duplicate proposals, prompt-injection content, and model failure.

**Step 2: Add explicit job and command surfaces**

Expose `ontology entities`, `ontology entity-create`, `ontology mine`,
`ontology candidates`, and review commands. Mining enqueues an idempotent
`ontology.mine` job; search and answer never invoke it.

**Step 3: Add API/MCP/SDK parity**

Create/list entities, enqueue mining, inspect candidates, approve/reject, and
explain assertions through the same application use cases.

**Step 4: Verify and commit**

```bash
.venv/bin/python scripts/generate_contracts.py
./scripts/verify.sh
git add src/kip/adapters/relation_miners src/kip/worker.py src/kip/cli.py \
  src/kip/api.py src/kip/mcp_server.py src/kip/container.py src/kip/domain/models.py \
  sdk/python/kip_client.py contracts tests/test_relation_miner_contract.py \
  tests/test_ontology_rag_cli.py
git commit -m "feat: mine ontology candidates through reviewed jobs"
```

### Task 12: Route approved graph evidence into answers

**Files:**
- Create: `src/kip/application/ontology_context.py`
- Create: `tests/test_ontology_answering.py`
- Create: `tests/integration/test_ontology_rag_postgres.py`
- Modify: `src/kip/application/search.py`
- Modify: `src/kip/application/answering.py`
- Modify: `src/kip/adapters/repository/postgres/knowledge.py`
- Modify: `src/kip/adapters/repository/memory/stores.py`
- Modify: `src/kip/domain/models.py`
- Modify: `src/kip/evaluation/models.py`

**Step 1: Write the full failing ontology RAG scenario**

Given two entities, exact source evidence, a reviewed assertion, an inaccessible
distractor, and a temporal contradiction, ask a graph question. Require only the
active visible assertion, exact underlying evidence citation, and the same result
from memory and PostgreSQL.

**Step 2: Verify red**

```bash
.venv/bin/pytest tests/test_ontology_answering.py \
  tests/integration/test_ontology_rag_postgres.py -q
```

**Step 3: Implement ontology-aware query planning**

Resolve named entities, retrieve approved temporally valid paths with ACL at
every hop, reopen assertion evidence, and add it to context with explicit path
metadata. Never cite a graph edge without exact evidence.

**Step 4: Run manual CLI/API ontology RAG QA**

Create entities, propose and approve one assertion, query neighbors and path,
ask an answer question, inspect citations, then repeat as a denied principal.

**Step 5: Verify and commit**

```bash
./scripts/verify.sh
git add src/kip/application/ontology_context.py src/kip/application/search.py \
  src/kip/application/answering.py src/kip/adapters/repository src/kip/domain/models.py \
  src/kip/evaluation/models.py tests/test_ontology_answering.py \
  tests/integration/test_ontology_rag_postgres.py
git commit -m "feat: answer from reviewed ontology evidence"
```

### Task 13: Materialize ontology migrations as review candidates

**Files:**
- Create: `src/kip/application/ontology_migrations.py`
- Create: `tests/test_ontology_migration_materialization.py`
- Modify: `src/kip/ontology_migration.py`
- Modify: `src/kip/ports/knowledge.py`
- Modify: `src/kip/adapters/repository/postgres/knowledge.py`
- Modify: `src/kip/adapters/repository/memory/stores.py`
- Modify: `src/kip/cli.py`

**Step 1: Write a failing migration materialization test**

A breaking rename or split must create target-version candidates with preserved
evidence and provenance, require review, leave approved source assertions
unchanged, and deduplicate repeated execution.

**Step 2: Implement and verify**

```bash
.venv/bin/pytest tests/test_ontology_migration_materialization.py -q
./scripts/verify.sh
```

**Step 3: Commit**

```bash
git add src/kip/application/ontology_migrations.py src/kip/ontology_migration.py \
  src/kip/ports/knowledge.py src/kip/adapters/repository src/kip/cli.py \
  tests/test_ontology_migration_materialization.py
git commit -m "feat: materialize ontology migrations for review"
```

## Milestone 6: Tracing, evaluation, and continuous quality

### Task 14: Add redacted query traces and operational metrics

**Files:**
- Create: `migrations/0010_query_traces.sql`
- Create: `src/kip/domain/telemetry.py`
- Create: `src/kip/ports/telemetry.py`
- Create: `src/kip/adapters/telemetry/__init__.py`
- Create: `src/kip/adapters/telemetry/otel.py`
- Create: `src/kip/adapters/telemetry/memory.py`
- Create: `tests/test_query_tracing.py`
- Modify: `src/kip/application/search.py`
- Modify: `src/kip/application/answering.py`
- Modify: `src/kip/application/ontology_rag.py`
- Modify: `src/kip/settings.py`
- Modify: `src/kip/container.py`
- Modify: `pyproject.toml`
- Modify: `config/kip.example.toml`

**Step 1: Write failing redaction and trace-shape tests**

Trace parsed route, stages, candidate IDs and ranks, selected evidence IDs, ACL
policy version, model revisions, warnings, latency, usage, and refusal reason.
Prove document bodies, credentials, raw provider payloads, and PII are absent.

**Step 2: Implement optional OpenTelemetry adapters**

The baseline works with a no-op or in-memory adapter. OTel dependencies are an
optional extra. Telemetry failure never changes answer semantics.

**Step 3: Verify and commit**

```bash
./scripts/verify.sh
git add migrations/0010_query_traces.sql src/kip/domain/telemetry.py \
  src/kip/ports/telemetry.py src/kip/adapters/telemetry src/kip/application \
  src/kip/settings.py src/kip/container.py pyproject.toml uv.lock \
  config/kip.example.toml tests/test_query_tracing.py
git commit -m "feat: trace RAG decisions without content leakage"
```

### Task 15: Extend evaluation to generated and ontology RAG quality

**Files:**
- Create: `src/kip/evaluation/ontology.py`
- Create: `tests/test_ontology_evaluation.py`
- Create: `evaluation/golden/ontology-starter.yaml`
- Modify: `src/kip/evaluation/answers.py`
- Modify: `src/kip/evaluation/models.py`
- Modify: `src/kip/evaluation/runner.py`
- Modify: `src/kip/evaluation/reporting.py`
- Modify: `evaluation/schemas/golden-case.schema.json`
- Modify: `evaluation/schemas/evaluation-report.schema.json`
- Modify: `docs/RAG_EVALUATION.md`

**Step 1: Write failing deterministic metric tests**

Add claim precision and recall, citation precision and recall, refusal
appropriateness, entity resolution, relation precision and recall,
contradiction detection, path relevance, temporal correctness, duplicate and
orphan counts, and ACL leakage. Unreviewed dimensions remain `null`.

**Step 2: Add dataset lifecycle fields**

Cases identify `draft`, `reviewed`, `golden`, `challenge`, or `canary` lifecycle,
split, version, reviewer, source revision, expected assertions, evidence, and
refusal. Only reviewed immutable versions may gate promotion.

**Step 3: Verify report fingerprints and fail-closed promotion**

```bash
.venv/bin/pytest tests/test_answer_evaluation.py \
  tests/test_ontology_evaluation.py tests/test_evaluation_runner.py -q
./scripts/verify.sh
```

**Step 4: Commit**

```bash
git add src/kip/evaluation tests/test_*evaluation.py evaluation docs/RAG_EVALUATION.md
git commit -m "feat: evaluate answer and ontology RAG quality"
```

## Milestone 7: Production packaging and acceptance

### Task 16: Harden Compose, roles, images, and release artifacts

**Files:**
- Modify: `Dockerfile`
- Modify: `compose.yaml`
- Create: `compose.production.yaml`
- Modify: `deploy/sql/roles.sql.template`
- Create: `scripts/release-bundle.sh`
- Create: `scripts/verify-release.sh`
- Create: `scripts/restore-drill.sh`
- Create: `tests/test_release_bundle.py`
- Modify: `Makefile`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/dependabot.yml`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/PRODUCTION_CHECKLIST.md`

**Step 1: Write failing release bundle tests**

Require wheel, API/worker/migration image references, digest-pinned Compose,
migrations, ontology, contracts, Skills, setup schemas, docs, SBOM, checksums,
and provenance. Reject secrets, private corpus paths, DB dumps, and CAS files.

**Step 2: Build least-privilege runtime profiles**

API and worker never use the owner role. Source mounts are read-only. Database,
MCP, model, and OCR services are private. Images run as a non-root user with a
read-only root filesystem where practical.

**Step 3: Add CI matrices and supply-chain checks**

Test Python 3.12/3.13, PostgreSQL 18, minimal and optional extras, contracts,
mypy, Ruff, tests, image builds, secret scan, dependency audit, SBOM, and release
bundle verification.

**Step 4: Run a local release build and restore drill**

```bash
./scripts/release-bundle.sh
./scripts/verify-release.sh
./scripts/restore-drill.sh
./scripts/verify.sh
```

**Step 5: Commit**

```bash
git add Dockerfile compose.yaml compose.production.yaml deploy scripts \
  tests/test_release_bundle.py Makefile .github docs/OPERATIONS.md \
  docs/PRODUCTION_CHECKLIST.md
git commit -m "build: harden private starter kit delivery"
```

### Task 17: Run blank-environment manual acceptance

**Files:**
- Create: `docs/acceptance/production-starter-kit-latest.md`
- Modify: `docs/IMPLEMENTATION_STATUS.md`
- Modify: `CHANGELOG.md`
- Modify: `VERSION`

**Step 1: Start from a blank temporary environment**

Do not reuse the development database, generated config, setup state, or CAS.

**Step 2: Drive setup as an AI agent user would**

Ask for KIP setup, answer only the returned questions, preview source scope,
approve the plan, apply, and verify.

**Step 3: Exercise real surfaces**

Run sample sync, search, generated or extractive answer, exact read, XLSX deep
read, allowed and denied ACL cases, entity creation, relation mining, review,
approved graph traversal, ontology-backed answer, provider failure, source
failure, parser failure, and database recovery.

**Step 4: Restore to new PostgreSQL and CAS locations**

Run migrations, rebuild projections, repeat RLS and golden canaries, and record
RTO/RPO observations.

**Step 5: Record evidence honestly**

The acceptance report includes exact commands, artifact fingerprints, pass/fail
counts, measured quality and latency, warnings, unverified external provider
paths, and remaining limitations. Do not claim the private 100-case gate unless
the reviewed private corpus actually exists and ran.

**Step 6: Final verification**

```bash
./scripts/verify.sh
git diff --check
```

**Step 7: Commit**

```bash
git add docs/acceptance/production-starter-kit-latest.md \
  docs/IMPLEMENTATION_STATUS.md CHANGELOG.md VERSION
git commit -m "docs: record production starter kit acceptance"
```

## Final completion gate

- Every commit has a fresh focused test and full `./scripts/verify.sh` pass.
- CLI, REST, MCP, and SDK preserve versioned semantic parity.
- Sources remain read-only and source hashes are unchanged.
- PostgreSQL non-owner RLS tests prove zero document and graph leakage.
- Generated claims cite exact fresh evidence or return typed refusal.
- Ontology mining creates candidates only; review creates approved assertions;
  graph answers reopen and cite assertion evidence.
- Setup runs from an absent config and asks only unresolved questions.
- Backup restores into a new database and CAS path.
- The release bundle contains no secrets, private corpus, database, or CAS data.
- The acceptance report distinguishes verified, partial, and unverified outcomes.
