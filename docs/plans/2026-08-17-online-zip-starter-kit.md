# Online ZIP Starter Kit Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build and verify a minimal, deterministic, internet-connected KIP source starter kit as a ZIP archive.

**Architecture:** A typed archive domain model defines the manifest contract. A focused builder/verifier module applies explicit inclusion and security policies directly to ZIP entries, while a small Typer edge exposes build and verify commands. Existing release-bundle behavior remains unchanged.

**Tech Stack:** Python 3.12+, Pydantic v2, Typer, standard-library `zipfile`, SHA-256, pytest, uv, Ruff, mypy.

---

### Task 1: Manifest contract

**Files:**
- Create: `src/kip/domain/starter_archive.py`
- Modify: `scripts/generate_contracts.py`
- Create: `contracts/starter-archive-manifest.schema.json`
- Test: `tests/test_starter_archive.py`

1. Add a failing test that parses the manifest and rejects unknown fields.
2. Run the test and confirm the contract is absent.
3. Add frozen Pydantic manifest and verification models using
   `kip.starter-archive.v1`.
4. Register and generate the JSON Schema.
5. Run the focused contract test and generated-contract check.

### Task 2: Deterministic builder and verifier

**Files:**
- Create: `src/kip/starter_archive.py`
- Test: `tests/test_starter_archive.py`

1. Add failing tests for the required file set, excluded private/local paths,
   complete checksums, deterministic repeated builds, and unsafe ZIP rejection.
2. Run each test red before implementation.
3. Implement the smallest explicit allowlist, text safety scan, manifest,
   checksum, deterministic ZIP, and verification flow.
4. Run the focused suite green, then Ruff and mypy on the new modules.

### Task 3: Operator commands

**Files:**
- Create: `src/kip/starter_archive_cli.py`
- Modify: `pyproject.toml`
- Create: `scripts/build-starter-kit.sh`
- Create: `scripts/verify-starter-kit.sh`
- Test: `tests/test_starter_archive.py`

1. Add a failing subprocess test for build and verify commands.
2. Expose `kip-starter-kit build` and `verify` through Typer.
3. Add repository wrappers that execute the locked uv environment.
4. Verify command exit codes, JSON envelopes, overwrite refusal, and dirty-tree
   opt-in behavior.

### Task 4: Canonical documentation

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/DATA_CONTRACTS.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/SECURITY.md`
- Modify: `docs/STARTER_KIT_GUIDE.md`
- Modify: `docs/IMPLEMENTATION_STATUS.md`
- Modify: `docs/PRODUCTION_DESIGN_ALIGNMENT.md`
- Create: `docs/adr/ADR-052-verified-online-source-zip.md`

1. Document the ZIP role, contents, verification command, online dependency
   boundary, security exclusions, and distinction from production releases.
2. Record the user-visible change and current readiness without overstating
   broad-production acceptance.

### Task 5: Artifact acceptance

**Files:**
- Create: `dist/kip-starter-kit-3.3.0.zip`
- Create: `dist/kip-starter-kit-3.3.0.zip.sha256`

1. Run focused tests, contract generation, Ruff, and mypy.
2. Build the actual ZIP from the current approved workspace with explicit
   dirty-tree opt-in and verify it without extraction.
3. Extract to a temporary directory and run shell syntax, project-structure,
   clean bootstrap/install, capabilities, and MCP smoke from the extracted kit.
4. Run `./scripts/verify.sh` in the source repository.
5. Report archive path, size, digest, file count, verification evidence, and
   remaining recipient-environment gates.
