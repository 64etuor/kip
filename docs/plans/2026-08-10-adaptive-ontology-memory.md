# Adaptive Ontology and Consent-Based Memory Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an empty starter domain profile plus consent-based clarification,
preference, feedback, and ontology-discovery workflows without weakening KIP's
evidence, ACL, or review boundaries.

**Architecture:** Keep generic ontology constraints in `ontology/core`; select a
domain YAML profile at composition time. Add a narrow `InteractionStore` port,
implemented by memory and PostgreSQL adapters, then expose one
`InteractionUseCases` object through every edge. Discovery candidates remain
separate from assertions and require an existing admin review surface.

**Tech Stack:** Python 3.12+, Pydantic v2, Typer, FastAPI, PostgreSQL/psycopg,
pytest, generated JSON Schema/OpenAPI.

---

### Task 1: Pin profile and consent contracts before implementation

**Files:**
- Create: `tests/test_ontology_profiles.py`
- Create: `tests/test_interactions.py`
- Modify: `tests/test_setup_planner.py`
- Modify: `tests/test_setup_writer.py`

**Step 1: Write failing profile tests**

Assert that `OntologyCatalog.load(root, domain_profile="empty")` accepts the
empty domain and excludes `OfficialLetter`, while the research profile retains
that type. Assert an unknown profile has a validation error.

**Step 2: Run the profile test red**

Run: `uv run pytest tests/test_ontology_profiles.py -q`
Expected: the loader does not accept `domain_profile` yet.

**Step 3: Write failing interaction behavior tests**

Cover a principal-created clarification, an explicit remembered response,
owner-only preference reads/deletes, an expired question, redacted feedback,
and a deduplicated discovery proposal that has no assertion side effect.

**Step 4: Run the interaction test red**

Run: `uv run pytest tests/test_interactions.py -q`
Expected: `InteractionUseCases` and the public request models do not exist.

**Step 5: Commit only after the corresponding green implementation**

Keep these tests with the capability implementation rather than committing a
deliberately failing branch.

### Task 2: Add selected ontology profiles and setup decisions

**Files:**
- Create: `ontology/domains/empty.yaml`
- Modify: `src/kip/ontology.py`
- Modify: `src/kip/container.py`
- Modify: `src/kip/cli.py`
- Modify: `src/kip/setup/models.py`
- Modify: `src/kip/setup/planner.py`
- Modify: `src/kip/setup/service.py`
- Modify: `src/kip/setup/writer.py`
- Modify: `config/kip.example.toml`
- Test: `tests/test_ontology_profiles.py`, `tests/test_setup_planner.py`,
  `tests/test_setup_writer.py`

**Step 1: Implement only profile selection**

Resolve a safe `ontology/domains/<profile>.yaml` path, validate it with the
core files and policy, and retain `core/<version>` as the catalog version.

**Step 2: Verify profile tests green**

Run: `uv run pytest tests/test_ontology_profiles.py -q`

**Step 3: Add setup answers and generated configuration**

Ask `ontology_profile` and `interaction_memory_mode` one at a time. Render
`[ontology] domain_profile` and `[interaction] enabled` only from the signed
plan. The example configuration selects `empty` and disables retention until
explicit consent is chosen.

**Step 4: Verify setup tests green**

Run: `uv run pytest tests/test_setup_models.py tests/test_setup_planner.py tests/test_setup_writer.py -q`

### Task 3: Add typed interaction models and capability port

**Files:**
- Create: `src/kip/domain/interactions.py`
- Create: `src/kip/ports/interactions.py`
- Create: `src/kip/application/interactions.py`
- Modify: `src/kip/application/runtime.py`
- Modify: `src/kip/ports/repository.py`
- Modify: `src/kip/container.py`
- Test: `tests/test_interactions.py`

**Step 1: Define bounded Pydantic input/read models**

Use fixed reason/outcome kinds, at most four choices, a safe preference-key
syntax, explicit `remember`/`confirmed` fields, short clarification TTLs, and
separate discovery candidate statuses. Do not add raw query, source, answer, or
free-text feedback fields to the event model.

**Step 2: Define the port around observable actions**

The port persists/gets/resolves clarifications, manages an owner preference,
records redacted feedback, manages discovery candidate idempotency, and lets an
admin review discovery state. It does not expose repository internals to the
application service.

**Step 3: Make memory-mode behavior green**

Use the real in-memory repository state rather than mocks. Run the focused
interaction test until the required consent, ownership, expiry, and discovery
boundaries pass.

### Task 4: Persist interaction state in PostgreSQL with RLS

**Files:**
- Create: `migrations/0015_interaction_memory.sql`
- Create: `src/kip/adapters/repository/memory/interactions.py`
- Create: `src/kip/adapters/repository/postgres/interactions.py`
- Modify: `src/kip/adapters/repository/memory/state.py`
- Modify: `src/kip/adapters/repository/memory/__init__.py`
- Modify: `src/kip/adapters/repository/postgres/__init__.py`
- Create or modify: PostgreSQL contract coverage in `tests/integration/test_postgres_repository.py`

**Step 1: Add an append-only migration**

Create interaction tables and indexes in a dedicated schema. Define
`kip.current_principal_id()` and use the adapter's trusted session setting for
owner RLS. Keep discovery candidates in a workspace reviewer queue and never
join them to assertion or graph tables.

**Step 2: Implement the memory adapter first**

Use deep copies and deterministic fingerprints. The response operation may
upsert a preference only when the stored clarification permits it and the user
sets `remember=true`.

**Step 3: Implement the PostgreSQL adapter transactionally**

Resolve a clarification, record the redacted event, and optionally upsert the
preference in one transaction. Use parameterized SQL, workspace/principal
constraints, and explicit owner checks.

**Step 4: Verify focused contracts**

Run: `uv run pytest tests/test_interactions.py tests/integration/test_postgres_repository.py -q`

### Task 5: Expose the shared service through CLI, REST, and MCP

**Files:**
- Modify: `src/kip/cli.py`
- Modify: `src/kip/api.py`
- Modify: `src/kip/mcp_server.py`
- Modify: `tests/test_cli_surface.py`
- Modify: `tests/test_api.py`
- Modify: `tests/characterization/test_edge_contract_parity.py`
- Test: end-to-end interaction parity cases

**Step 1: Write an edge-parity test red**

Create a clarification through the CLI and REST paths against one shared
container; answer it with `remember=true`; assert the resulting preference has
the same typed contents. Add MCP tool discovery/assertion coverage where the
optional MCP test infrastructure is available.

**Step 2: Add thin edge adapters**

CLI commands, REST routes, and MCP tools must only construct request models and
call `application.interactions`. REST discovery review uses the existing admin
dependency; CLI uses its local admin invocation convention.

**Step 3: Verify parity green**

Run: `uv run pytest tests/test_cli_surface.py tests/test_api.py tests/characterization/test_edge_contract_parity.py -q`

### Task 6: Regenerate contracts, document operations, and verify a user flow

**Files:**
- Modify: `scripts/generate_contracts.py`
- Regenerate: `contracts/*.schema.json`, `contracts/openapi.json`, `contracts/openapi.yaml`
- Create: `docs/adr/ADR-032-consent-based-interaction-memory.md`
- Modify: `docs/DATA_CONTRACTS.md`
- Modify: `docs/SECURITY.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/APP_INTEGRATION.md`
- Modify: `docs/IMPLEMENTATION_STATUS.md`
- Modify: `docs/STARTER_KIT_GUIDE.md`
- Modify mirrored: `skills/knowledge-fabric/**`, `.claude/skills/knowledge-fabric/**`

**Step 1: Describe the non-automatic boundary**

State that user preferences are not assertions, feedback is not a golden case,
and a discovery candidate cannot change active YAML or graph data.

**Step 2: Regenerate checked-in contracts**

Run: `uv run python scripts/generate_contracts.py`, then
`uv run python scripts/generate_contracts.py --check`.

**Step 3: Run a manual CLI scenario**

Use a memory-backed test configuration to create and answer one clarification,
read the remembered preference, submit feedback, create a discovery candidate,
and confirm the regular ontology assertion candidate list stays empty.

**Step 4: Run the final gate and commit atomically**

Run: `./scripts/verify.sh`.

Stage only implementation, tests, migrations, generated contracts, and docs for
this feature. Commit with the repository's conventional commit style.
