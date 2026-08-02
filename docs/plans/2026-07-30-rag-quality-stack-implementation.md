# Local-first RAG Quality Stack Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build and actually evaluate KIP's pre-Neo4j local RAG stack with reproducible golden metrics, pgvector hybrid retrieval, local reranking, and an append-only quality-evolution record.

**Architecture:** PostgreSQL remains canonical while a 1024-dimensional pgvector table stores disposable semantic projections. Provider-neutral HTTP ports call a persistent local Infinity sidecar, `KnowledgeService` performs reciprocal-rank fusion and optional reranking, and a deterministic evaluator generates JSON/Markdown reports from versioned YAML golden cases.

**Tech Stack:** Python 3.12+, Pydantic, Typer, psycopg 3, PostgreSQL 18, pgvector 0.8+, httpx, PyYAML, Infinity, Sentence Transformers, Qwen3 Embedding 0.6B, BGE reranker v2 M3, pytest.

---

The current extracted bundle has no `.git` directory. In this checkout, replace each commit step with a verification checkpoint and record changed-file hashes in the evaluation report. When the repository is later placed under Git, use the listed commit boundaries.

## Execution record

Implementation completed with a few evidence-driven path changes:

- the manifest is `evaluation/corpus/public-government.json`;
- the fetcher is `scripts/fetch_public_corpus.py` and downloads verified PDFs to
  gitignored `var/public-corpus/`;
- the reviewed dataset is
  `evaluation/golden/public-government.yaml`, containing 30 relevance and six
  ACL-denial cases;
- generated evidence is under
  `evaluation/reports/public-government/`, with the append-only ledger at
  `evaluation/reports/evolution.jsonl`;
- Qwen3 Embedding 0.6B and BGE reranker v2 M3 were run on the real corpus.
  BGE-M3 as a second embedding model was deferred because the corrected
  lexical baseline saturated this pilot and the first semantic candidate did
  not pass the promotion gate;
- exact run IDs, scores, changes, and the `keep_disabled` decision are recorded
  in `docs/RAG_EVALUATION.md`.

### Task 1: Record the architecture decision and evaluation contracts

**Files:**
- Create: `docs/adr/ADR-005-local-first-hybrid-retrieval.md`
- Create: `evaluation/schemas/golden-case.schema.json`
- Create: `evaluation/schemas/evaluation-report.schema.json`
- Create: `evaluation/README.md`
- Modify: `docs/TRD.md`

**Step 1: Write failing contract tests**

Create `tests/test_evaluation_contracts.py` with cases that load both schemas, validate a minimal golden case and report, and reject a golden case without `expected_documents`.

**Step 2: Verify red**

Run:

```bash
./scripts/test.sh tests/test_evaluation_contracts.py -v
```

Expected: failure because the schema files do not exist.

**Step 3: Add minimal contracts and ADR**

The golden case requires `id`, `question`, `category`, `principal`, `acl_scopes`, `expected_documents`, `forbidden_documents`, and `recall_at`. The report requires run metadata, corpus/golden/config fingerprints, variants, aggregate metrics, per-category metrics, latency, gates, and decision.

Add the accepted sidecar, model-candidate, ACL, evaluation, and promotion decisions to ADR-005. Link this design and ADR from the TRD evaluation section.

**Step 4: Verify green**

Run the targeted test and `python scripts/generate_contracts.py --check`.

**Step 5: Checkpoint**

Commit message when Git is available: `docs: define local RAG evaluation contracts`

### Task 2: Implement the deterministic evaluator core

**Files:**
- Create: `src/kip/evaluation/__init__.py`
- Create: `src/kip/evaluation/models.py`
- Create: `src/kip/evaluation/metrics.py`
- Create: `src/kip/evaluation/reporting.py`
- Create: `tests/test_evaluation_metrics.py`
- Create: `tests/test_evaluation_reporting.py`

**Step 1: Write failing metric tests**

Cover:

- document deduplication before ranking;
- Recall@K for multiple relevant documents;
- reciprocal rank with no hit and hit at rank 2;
- binary nDCG@K;
- forbidden-document counting;
- locator constraint matching;
- percentile calculation for one and multiple samples.

**Step 2: Verify red**

Run:

```bash
./scripts/test.sh tests/test_evaluation_metrics.py tests/test_evaluation_reporting.py -v
```

Expected: import failures for `kip.evaluation`.

**Step 3: Implement pure metric functions**

Use stable document identity when available and fall back to unit identity only for a case that explicitly expects units. Metrics must be deterministic and must not read global configuration or a database.

**Step 4: Implement report rendering and evolution records**

`write_report()` writes timestamped JSON and Markdown plus atomically replaces `latest.json` and `latest.md`. `append_evolution_record()` appends one compact JSON object and calls `flush()` plus `os.fsync()` before close.

**Step 5: Verify green**

Run targeted tests and Ruff on the new package.

**Step 6: Checkpoint**

Commit message: `feat: add deterministic RAG evaluation metrics`

### Task 3: Add evaluator application service and CLI

**Files:**
- Create: `src/kip/evaluation/runner.py`
- Modify: `src/kip/cli.py`
- Create: `tests/test_evaluation_runner.py`
- Modify: `tests/test_cli_surface.py`
- Modify: `Makefile`

**Step 1: Write failing runner and CLI tests**

Use `MemoryRepository` fixtures with at least four documents and cases for exact, paraphrase, forbidden, and locator checks. Assert:

- dataset validation rejects duplicate case IDs;
- a lexical run returns all required metrics;
- report filenames and hashes are stable;
- `kip evaluate validate`, `run`, and `compare` retain the versioned JSON envelope;
- `--variant` names are validated.

**Step 2: Verify red**

Run:

```bash
./scripts/test.sh tests/test_evaluation_runner.py tests/test_cli_surface.py -v
```

Expected: missing evaluator commands and runner.

**Step 3: Implement the runner**

The runner receives a search callable, context factory, clock, and output path. It loads YAML with `yaml.safe_load`, resolves expected source-relative paths to stable document IDs through the repository, times each query with `time.perf_counter_ns`, and never stores document body text in a report.

**Step 4: Add CLI and Make target**

Add a Typer `evaluate` group with:

- `validate --dataset PATH`;
- `run --dataset PATH --variants lexical,hybrid,reranked --output-dir PATH`;
- `compare --baseline VARIANT --candidate VARIANT --report PATH`.

Add `make evaluate` using the public pilot dataset and configured variants.

**Step 5: Verify green and manually QA**

Run the targeted tests, then invoke `./scripts/kip evaluate --help` and validate a fixture dataset through the real CLI.

**Step 6: Checkpoint**

Commit message: `feat: add reproducible RAG evaluation CLI`

### Task 4: Define embedding and reranking ports with HTTP adapters

**Files:**
- Modify: `src/kip/ports/embedding.py`
- Create: `src/kip/ports/reranker.py`
- Create: `src/kip/adapters/embeddings/http.py`
- Create: `src/kip/adapters/rerankers/__init__.py`
- Create: `src/kip/adapters/rerankers/http.py`
- Modify: `src/kip/adapters/embeddings/noop.py`
- Create: `tests/test_model_http_adapters.py`

**Step 1: Write failing adapter tests**

Use `httpx.MockTransport` to assert:

- query and document embedding requests use the configured model;
- returned dimension must equal the configured dimension;
- rerank results are sorted by index and score safely;
- timeouts and malformed responses raise `DependencyUnavailableError`;
- no request is made when an adapter is disabled;
- only loopback URLs are accepted while remote model egress is false.

**Step 2: Verify red**

Run:

```bash
./scripts/test.sh tests/test_model_http_adapters.py -v
```

**Step 3: Implement ports and adapters**

The embedding port exposes `embed_query(text)` and `embed_documents(texts)` plus immutable provider/model/revision/dimensions/normalization metadata. The reranker exposes `rerank(query, documents)` returning input indexes and float scores. HTTP adapters use injected `httpx.Client` objects for testability.

**Step 4: Verify green**

Run targeted tests and Ruff.

**Step 5: Checkpoint**

Commit message: `feat: add local model HTTP adapters`

### Task 5: Add the 1024-dimensional pgvector projection

**Files:**
- Create: `migrations/0006_pgvector_1024_projection.sql`
- Modify: `src/kip/ports/repository.py`
- Modify: `src/kip/adapters/repository/postgres.py`
- Modify: `src/kip/adapters/repository/memory.py`
- Modify: `src/kip/domain/models.py`
- Modify: `tests/integration/test_postgres_repository.py`
- Create: `tests/test_semantic_repository_contract.py`

**Step 1: Write failing repository contract tests**

Specify:

- embedding spaces can coexist and only one can be active;
- vectors retain `source_hash`;
- stale vectors are excluded;
- semantic candidates are ACL-prefiltered;
- exact vector search returns stable `SearchHit` contracts;
- deleting/rebuilding one space does not touch canonical units or another space.

**Step 2: Verify red**

Run memory contract tests first. Run PostgreSQL integration with `KIP_TEST_POSTGRES_URL` and confirm failure before migration/code exists.

**Step 3: Add append-only migration**

Create the `vector` extension, `search.embeddings_1024`, workspace RLS, an ACL-safe search path through joins to `content.units`, and metadata indexes. Do not add HNSW.

**Step 4: Implement repository methods**

Add methods to list current embeddable units, upsert a named space in batches, activate a qualifying space transactionally, count vectors, and perform exact cosine search with all existing request filters and ACL conditions applied in SQL.

**Step 5: Verify green**

Run both memory and PostgreSQL contract tests. Query `pg_extension`, table dimensions, policies, and row counts directly.

**Step 6: Checkpoint**

Commit message: `feat: add ACL-safe pgvector projection`

### Task 6: Implement semantic rebuild, RRF fusion, and reranking

**Files:**
- Create: `src/kip/application/retrieval.py`
- Modify: `src/kip/application/services.py`
- Modify: `src/kip/container.py`
- Modify: `src/kip/settings.py`
- Modify: `src/kip/cli.py`
- Modify: `src/kip/worker.py`
- Create: `tests/test_hybrid_retrieval.py`
- Modify: `tests/test_worker.py`

**Step 1: Write failing behavior tests**

Cover:

- RRF uses rank, not raw score;
- duplicate units fuse into one hit and preserve channel metadata;
- lexical exact hits remain stable on ties;
- reranker receives only the fused top-N;
- disabled semantic mode performs no model calls;
- optional sidecar outage degrades to lexical with an explicit warning;
- evaluator-required semantic variants fail instead of silently falling back;
- rebuild failure leaves the old active space unchanged.

**Step 2: Verify red**

Run targeted tests and capture the expected missing behavior.

**Step 3: Implement minimal retrieval pipeline**

Keep RRF as a pure function. Inject embedding and reranker ports into `KnowledgeService`. Add `search_mode` internally so evaluator variants can request lexical, vector, hybrid, or reranked behavior without changing the public default contract.

**Step 4: Implement semantic rebuild**

Read current active units in deterministic ID order, build model input from title plus normalized body, embed in configured batches, validate dimension and finite values, then upsert with source hashes. Activation is a separate operation.

**Step 5: Wire CLI, worker, status, and capabilities**

Support semantic projection rebuild/verify/status, report served models and health without leaking URLs or keys, and preserve the lexical-only startup path.

**Step 6: Verify green and manual QA**

Run targeted tests, then use a fake local HTTP server with the real CLI to rebuild and query a small PostgreSQL workspace.

**Step 7: Checkpoint**

Commit message: `feat: add hybrid retrieval and reranking`

### Task 7: Add local semantic installation and operations

**Files:**
- Modify: `pyproject.toml`
- Create: `scripts/bootstrap-semantic.sh`
- Create: `scripts/semantic-server.sh`
- Create: `scripts/semantic-smoke.sh`
- Modify: `scripts/doctor.sh`
- Modify: `config/kip.example.toml`
- Modify: `config/kip.container.toml`
- Modify: `.env.example`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/SECURITY.md`
- Create: `tests/test_semantic_scripts.py`

**Step 1: Write failing script/config tests**

Assert shell syntax, loopback binding, pinned served model names, remote-egress denial, health check behavior, and an isolated optional virtual environment under `var/semantic-venv`.

**Step 2: Verify red**

Run the script test and `bash -n` checks.

**Step 3: Add scripts and configuration**

Install Infinity only into `var/semantic-venv`, keep model caches configurable under `var/model-cache`, launch Qwen3 Embedding 0.6B plus BGE reranker v2 M3 on `127.0.0.1:7997`, and write PID/log files under `var/run` and `var/log`.

**Step 4: Extend doctor**

Report dependency installation, free disk, sidecar health, served models, configured dimensions, pgvector extension, active space, parity, and whether semantic is shadow or active.

**Step 5: Verify green**

Run script/config tests and `./scripts/doctor.sh` with the server stopped to confirm optional degradation is reported correctly.

**Step 6: Checkpoint**

Commit message: `ops: add local semantic model sidecar`

### Task 8: Acquire a safe public Korean government pilot corpus

**Files:**
- Create: `evaluation/corpus/public-government.json`
- Create: `scripts/fetch_public_corpus.py`
- Create: `tests/test_public_corpus.py`
- Modify: `config/kip.example.toml`

**Step 1: Write failing fetcher tests**

Mock HTTP to cover HTTPS-only URLs, allowed media types, redirect validation, checksum pinning, atomic replacement, license/attribution requirements, and preservation of an existing valid file after a failed refresh.

**Step 2: Verify red**

Run the targeted fetcher tests.

**Step 3: Implement the safe fetcher**

Download only manifest entries. Store checksum-verified files under the
gitignored `var/public-corpus/`. Never enumerate OneDrive or follow arbitrary
page links.

**Step 4: Curate the manifest**

Select six public Korean government PDFs with explicit Public Nuri Type 1
terms. Pin checksums after the first reviewed download and record attribution.

**Step 5: Add disabled-by-default source configuration**

Add `public-government` as a disabled-by-default, read-only filesystem source
pointing only to `var/public-corpus`.

**Step 6: Verify and manually inspect**

Run the fetcher, verify hashes and detected formats, inspect at least one rendered page from each PDF family, and confirm no cloud-storage path appears in the lock file.

**Step 7: Checkpoint**

Commit message: `testdata: add licensed Korean public RAG corpus`

### Task 9: Create the 30-case public pilot golden set

**Files:**
- Create: `evaluation/golden/public-government.yaml`
- Create: `tests/test_public_corpus.py`
- Create: `docs/RAG_EVALUATION.md`

**Step 1: Write failing dataset quality tests**

Require at least 30 cases, unique IDs, all mandatory categories, at least five semantic paraphrases, at least three access-denied cases, stable expected locators, and no unanswered/generated expectations.

**Step 2: Verify red**

Run the dataset quality test.

**Step 3: Create cases from inspected evidence**

Enumerate stable document IDs after ingestion. Human-review each expected
document against extracted PDF text and retain the reviewed facts in the
question set and evaluation record.

**Step 4: Verify green**

Validate schema plus dataset quality and run the lexical baseline once.

**Step 5: Checkpoint**

Commit message: `testdata: add Korean public retrieval golden set`

### Task 10: Benchmark candidate stacks and record the activation decision

**Files:**
- Generated: `evaluation/reports/public-government/<run-id>.json`
- Generated: `evaluation/reports/public-government/<run-id>.md`
- Generated: `evaluation/reports/evolution.jsonl`
- Create: `docs/RAG_EVALUATION.md`
- Modify: `docs/IMPLEMENTATION_STATUS.md`

**Step 1: Establish lexical baseline**

Run all 30 cases at least three times for latency, preserving one quality record and the latency sample distribution.

**Step 2: Build the Qwen3 shadow space**

Rebuild the pinned Qwen3 space, verify parity and model/config fingerprints,
and keep it inactive. Evaluate BGE-M3 as a second embedding space only if the
first candidate exposes a recall gap that can justify the extra model and
runtime cost.

**Step 3: Evaluate vector, hybrid, and reranked variants**

Use identical cases and limits. Compare overall and category scores, exact-query regression, unauthorized results, stale warnings, storage, rebuild time, and p95 latency.

**Step 4: Apply the promotion gate**

Activate only a configuration that passes every mandatory security/correctness gate and either overall or semantic-category improvement threshold. Otherwise keep semantic disabled and state why.

**Step 5: Record evolution evidence**

Append reports and a concise decision record. Update `IMPLEMENTATION_STATUS.md` with measured current status rather than a model marketing claim.

**Step 6: Checkpoint**

Commit message: `docs: record measured RAG activation decision`

### Task 11: Full verification and recipient handoff

**Files:**
- Modify: `README.md`
- Modify: `docs/QUICKSTART.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/PRODUCTION_CHECKLIST.md`
- Modify: `docs/IMPLEMENTATION_STATUS.md`

**Step 1: Document clean-machine and recurring workflows**

Document core bootstrap, optional semantic bootstrap, server lifecycle, corpus fetch, migration, sync, projection rebuild, evaluation, report interpretation, scheduled execution, cleanup, and the OneDrive allowlist rule.

**Step 2: Run static and automated verification**

Run:

```bash
./scripts/verify.sh
.venv/bin/mypy src
KIP_TEST_POSTGRES_URL="$KIP_DATABASE_URL" ./scripts/test.sh tests/integration/test_postgres_repository.py -v
```

Changed-file diagnostics must be clean. Existing unrelated strict-mypy debt must be listed separately if it remains.

**Step 3: Run the real recipient path**

From the documented commands:

1. start PostgreSQL;
2. start the local model sidecar;
3. fetch the public corpus;
4. ingest it;
5. rebuild a shadow semantic projection;
6. execute lexical, hybrid, and reranked evaluation;
7. open the generated Markdown scorecard;
8. run one exact query, one semantic paraphrase, one forbidden query, and one exact evidence read.

**Step 4: Confirm source safety**

Compare source corpus hashes before and after indexing, confirm only allowlisted public files were read, and verify the evolution ledger contains no source bodies, secrets, or private filesystem paths.

**Step 5: Final checkpoint**

Commit message: `docs: complete reproducible RAG quality handoff`
