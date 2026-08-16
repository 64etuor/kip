# Parser Resource Isolation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and superpowers:verification-before-completion task-by-task.

**Goal:** Run every filesystem document parser in a bounded child process tuned for serial NAS indexing on an M4 Pro with 24 GB unified memory.

**Architecture:** Keep `ParserPort` unchanged and wrap each concrete parser at composition time with an `IsolatedParserAdapter`. A fresh Python child reconstructs the selected concrete parser without the wrapper, applies POSIX resource limits before parsing, writes a versioned Pydantic-validated response to a private result file, and exits. The parent owns wall-clock termination, process-group cleanup, bounded diagnostics, and response validation.

**Tech Stack:** Python 3.13, `subprocess`, POSIX `resource`, Pydantic v2, pytest, existing KIP parser/domain contracts.

---

### Task 1: Lock the child contract and failure behavior

**Files:**
- Create: `tests/test_parser_isolation.py`
- Create: `src/kip/adapters/parsers/isolation.py`

1. Add failing tests for versioned response round-trip, timeout process termination, oversized response rejection, non-zero child exit, and sanitized bounded stderr.
2. Run `uv run pytest tests/test_parser_isolation.py -q` and capture the expected import/behavior failures.
3. Add frozen Pydantic request/response models and typed isolation settings sufficient to satisfy serialization-only tests.
4. Run the focused tests and keep the red cases for process supervision.

### Task 2: Implement the bounded child runtime

**Files:**
- Modify: `tests/test_parser_isolation.py`
- Create: `tests/test_process_supervisor.py`
- Modify: `src/kip/adapters/parsers/isolation.py`
- Create: `src/kip/adapters/parsers/process_supervisor.py`
- Create: `src/kip/adapters/parsers/isolated_worker.py`

1. Add failing integration tests using a deterministic child mode that sleeps, allocates beyond its limit, writes an oversized result, and returns a valid parser response.
2. Implement private temporary result/diagnostic files, `start_new_session=True`, parent wall timeout, process-group TERM/KILL cleanup, bounded reads, and typed `ParserError` conversion.
3. In the child, apply CPU/file-size/open-file/core limits and configured
   thread-count environment before importing parser libraries. Supervise the
   full process-tree RSS from the parent on macOS and Linux; add address/data
   rlimits on Linux where they are enforceable.
4. Validate request/response through Pydantic models; never use pickle or trust child JSON directly.
5. Run focused tests until green.

### Task 3: Preserve ParserPort composition

**Files:**
- Modify: `src/kip/adapters/parsers/registry.py`
- Modify: `src/kip/container.py` only if composition validation requires it
- Modify: `tests/test_parser_isolation.py`
- Modify: parser registry/settings tests as required

1. Add failing tests proving `ParserRegistry.from_settings()` wraps registered parsers when isolation is enabled and constructs raw parsers in the child without recursive wrapping.
2. Extract a minimal raw-parser factory keyed by stable adapter key.
3. Wrap concrete parsers with `IsolatedParserAdapter` only at the outer registry composition boundary.
4. Preserve parser `name`, `version`, `supports()`, representation roles, and returned domain models exactly.
5. Run registry and all parser tests.

### Task 4: Add the M4 Pro 24 GB profile and synchronized docs

**Files:**
- Modify: `config/kip.toml`
- Modify: `config/kip.example.toml`
- Modify: `config/kip.container.toml`
- Create: `src/kip/setup/config_payload.py`
- Modify: `src/kip/setup/writer.py`
- Modify: `docs/TRD.md`
- Modify: `docs/SECURITY.md`
- Modify: `docs/CONNECTORS.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/IMPLEMENTATION_STATUS.md`
- Modify: `docs/PRODUCTION_DESIGN_ALIGNMENT.md`
- Modify: `CHANGELOG.md`
- Create: `docs/adr/ADR-050-bounded-filesystem-parser-processes.md`

1. Add validated `[parsers.isolation]` settings with defaults: enabled, serial
   execution, 4 CPU threads, 6 GiB aggregate process-tree RSS, 120 CPU seconds,
   180 wall seconds, 256 MiB result, bounded diagnostics, and low child priority.
2. Document macOS/Linux behavior, explicit residual network/filesystem boundaries, timeout failure semantics, and the need to re-extract only when parser output/version changes.
3. Record the adapter-boundary decision and rejected persistent-pool/container alternatives in ADR-050.
4. Run contract/doc consistency checks.

### Task 5: Measure real NAS-format behavior and complete verification

**Files:**
- Modify: implementation/status docs only if measured values change the defaults

1. Select privacy-safe representative local files for PDF, DOCX, PPTX, XLSX/XLSM, HWP/HWPX without reporting names or content.
2. Run raw versus isolated parser invocations with source SHA before/after, elapsed time, exit status, unit/status equality, and child peak RSS where the OS exposes it.
3. Adjust only the resource profile if observed headroom is inadequate; rerun the affected measurements.
4. Run targeted parser suites, `ruff`, `mypy`, generated contracts, and `./scripts/verify.sh`.
5. Inspect `git diff`, source hashes, and the debug journal; remove temporary benchmark artifacts and the journal before handoff.
