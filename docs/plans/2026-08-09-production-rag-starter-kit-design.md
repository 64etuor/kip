# Production RAG Starter Kit Design

**Status:** Approved  
**Date:** 2026-08-09  
**Deployment profile:** Organization self-hosted, single tenant  
**Distribution:** Controlled private handoff  
**Product surface:** Headless CLI, REST, MCP, and Python SDK

## Objective

Turn KIP from a strong evidence-first retrieval pilot into a production RAG
starter kit that another organization can install, configure, operate, evaluate,
upgrade, and recover without weakening source immutability, ACL enforcement,
evidence locators, or assertion review.

The implementation may replace internal modules and Python import paths. Public
IDs, versioned JSON and OpenAPI semantics, immutable source revisions, approved
assertions, and append-only migrations remain stable.

## Product decisions

- PostgreSQL remains the canonical operational store.
- Lexical, vector, graph, summary, and trace data remain rebuildable projections.
- The standard deployment uses Docker Compose with PostgreSQL, API, worker, and
  migration processes. Model and OCR services are optional sidecars.
- An identity-aware proxy or approved service gateway establishes the trusted
  principal boundary.
- External OpenAI and Anthropic providers may be selected during setup. Egress is
  allowed only when the provider, data classification, retention policy, secret
  reference, model revision, timeout, and cost limit are configured.
- There is no web or chat UI in the starter kit. Every capability is available
  through stable machine-readable interfaces.
- Distribution is private, but release bundles never contain credentials,
  company paths, private corpora, database dumps, or CAS contents.

## Non-goals

- Replacing PostgreSQL with a vector or graph database.
- Treating retrieval, Graphify, parser, model, or relation-miner output as an
  approved fact.
- Answering material questions from snippets or stale source revisions.
- Calculating spreadsheet facts from the shallow lexical index.
- Triggering sync, re-indexing, embedding, graphification, or projection rebuild
  during normal search or answer requests.
- Adding a frontend before the headless contracts and operational gates pass.

## Architecture

```text
CLI · REST · MCP · Python SDK · Worker
                 |
        trusted RequestContext
                 |
        application use cases
  +--------+--------+----------+---------+
  | ingest | search | answer   | review  |
  | sync   | read   | evaluate | operate |
  +--------+--------+----------+---------+
                 |
         capability ports
                 |
 PostgreSQL · Sources · Parsers · LLM · OCR
 Identity · Telemetry · Object Storage
```

### Application boundaries

The current `KnowledgeService` is replaced by focused use cases such as
`SyncSource`, `IngestRevision`, `Search`, `BuildContext`, `Answer`,
`ReadEvidence`, `ReviewAssertion`, `EvaluateCandidate`, and `OperateProjection`.
CLI, REST, MCP, SDK, and workers invoke these same use cases.

The current repository protocol is split by capability: ingestion, retrieval,
evidence, identity and ACL, assertions, jobs, evaluation, and operations. A
PostgreSQL unit of work combines capabilities only when one canonical
transaction is required.

Domain models are organized by source, evidence, retrieval, assertion,
evaluation, and operations aggregates. Public boundaries use versioned typed
models instead of arbitrary dictionaries. Domain and application modules import
ports, never concrete adapters or vendor SDKs.

The composition root is the only location that selects concrete PostgreSQL,
connector, parser, model, OCR, identity, and telemetry adapters.

### Runtime topology

- `api`: REST and optional MCP-over-approved-transport edge.
- `worker`: source sync, parsing, OCR, projection, evaluation, and relation jobs.
- `migrate`: one-shot append-only database migration process.
- `postgres`: canonical state and active lexical projection.
- optional model/OCR sidecars: loopback or private-network only.
- identity-aware proxy: authenticates callers and supplies verified claims.

Workers use transactional outbox records and idempotency keys. Query processes
never execute maintenance jobs synchronously.

## Ingestion and projection flow

```text
read-only source
  -> change and ACL snapshot
  -> immutable raw capture and hash
  -> shadow extraction
  -> quality and locator checks
  -> atomic activation
  -> idempotent projection jobs
```

Each connector emits a stable external ID, immutable revision, tombstone state,
ACL snapshot, and cursor. A source outage is never interpreted as mass deletion.
Raw bytes are captured in content-addressed storage before parsing.

Parser and OCR outputs are shadow extractions. A failed, timed-out, malformed,
or low-quality extraction cannot replace the previous active extraction.
Canonical activation and outbox creation occur in one PostgreSQL transaction.

Lexical, vector, graph, and summary jobs are independently rebuildable. Every
candidate records the source revision, adapter and model revision,
configuration, code fingerprint, and corpus fingerprint that produced it.

Source ACL snapshots have a version, captured time, and freshness policy. A
document with stale or unresolved identity mapping is excluded fail-closed.

## Retrieval and answer flow

```text
trusted identity
  -> typed query plan
  -> ACL-prefiltered retrieval
  -> fusion and optional reranking
  -> exact evidence read and freshness check
  -> bounded generation
  -> claim-to-citation verification
  -> answer or refusal plus trace
```

The query planner classifies exact identifiers, lexical questions, semantic
paraphrases, spreadsheet questions, and graph or cross-document questions.
Planning is explicit and traceable. Exact lexical retrieval remains the active
baseline until a fingerprint-matched evaluation promotes another projection.

ACL filters execute before lexical search, vector search, and every graph hop.
Search hits are discovery candidates, not answer evidence. Selected units are
reopened from their exact source revision and locator, and current hashes are
compared with indexed hashes before generation.

Spreadsheet numbers, formulas, dates, and totals come only from an exact range
read of the original workbook. Shallow XLSX cards remain discovery-only.

A provider-neutral generator port supports an approved local OpenAI-compatible
gateway and selected external OpenAI or Anthropic adapters. Generation receives
only the bounded evidence allowed by egress policy. The provider response is a
typed list of claims with citation references, not unstructured prose alone.

Each material claim must be directly supported by one or more exact evidence
locators. Unsupported claims are omitted or marked uncertain. If sufficient
fresh evidence is unavailable, KIP returns a typed refusal instead of guessing.

Responses include citation locators, source and current hashes, stale state,
retrieval trace ID, generator and model revisions, latency, token usage, and cost
where available.

## Ontology RAG

Ontology RAG is an end-to-end production capability, not a YAML validation
feature or an automatically generated knowledge graph.

```text
evidence units
  -> entity resolution candidates
  -> relation candidates with provenance
  -> ontology and policy validation
  -> human review when required
  -> approved assertions
  -> ACL and temporal graph retrieval
  -> exact assertion evidence read
  -> cited answer
```

Ontology files remain the meaning contract. Database enums, model labels, and
Neo4j labels cannot become the canonical ontology.

Deterministic source relations such as message replies, attachments, and
document representations are stored separately from semantic assertions. Model,
Graphify, parser, and relation-miner outputs always remain candidates until the
applicable review policy approves them.

Entity and relation candidates use stable idempotency fingerprints over the
ontology version, source revision, evidence locator, normalized subject,
predicate, object, and miner revision. Reprocessing cannot create duplicate
candidates.

Candidate validation checks ontology version, domain and range, evidence
presence, ACL intersection, temporal bounds, contradiction policy, and review
risk. Approval records the reviewer, note, timestamp, evidence, and effective
interval in one transaction.

Graph retrieval uses only approved, temporally valid assertions whose evidence
is visible to the principal. ACL is applied at every traversal step. A graph
path is an explanation aid, not a citation by itself; answers must reopen the
underlying evidence units and cite their exact locators.

Ontology migrations never rewrite approved assertions. Compatible releases may
add symbols. Review-required and breaking releases create target-version
candidates through explicit migration manifests and preserve the previous
release for audit and rollback.

PostgreSQL remains the graph implementation until a graph-heavy reviewed corpus
shows that a Neo4j projection improves relevance, latency, traversal depth, ACL
parity, and rebuild cost.

Ontology RAG release gates include entity-resolution quality, relation precision
and recall, contradiction detection, path relevance, temporal correctness,
duplicate and orphan rates, citation support, and zero ACL leakage. The broad
rollout corpus contains at least 100 reviewed assertions and graph questions.

## Agent-guided setup

The user can tell Codex, Claude, or another compatible agent only: “Set up KIP.”
A portable `kip-setup` Skill drives a deterministic setup state machine.

```text
kip setup inspect
  -> agent asks only missing decisions, one at a time
kip setup plan --answers ...
  -> user reviews mounts, changes, checks, and risks
kip setup apply --plan ...
  -> CLI writes generated configuration atomically
kip setup verify
  -> live canaries and a redacted acceptance receipt
```

The agent asks about workspace and identity ownership, source folders and
exclusions, data classifications and ACL mapping, connectors, model providers
and egress, database and CAS, backup, retention, scheduling, evaluation data,
and ontology review ownership.

The agent never writes TOML, Compose, or credentials directly. Versioned answer
schemas are parsed by the CLI. Setup state contains no secrets and can resume
after interruption.

The setup command rejects `/`, an entire home directory, and an entire workspace
as source roots. It requires named subdirectories, canonicalizes paths, previews
file count, formats, size, and exclusions, and obtains approval before collection.
Compose mounts source folders read-only. Native collection that cannot prove a
read-only boundary requires an explicit warning and approval.

Personal and company sources are separated into different workspaces or
deployments. Credentials are represented by secret-manager, keychain, or
environment references. Provider connectivity tests use synthetic text until a
data classification explicitly permits document egress.

Setup apply uses atomic writes and retains the previous generated configuration
for rollback. It never mutates sources or canonical data.

## Failure handling and security

Boundary failures use typed error categories: authentication, authorization,
stale identity or ACL, source unavailable, parser quarantine, model unavailable,
insufficient evidence, stale evidence, exact spreadsheet read required,
configuration, conflict, and dependency unavailable.

- Missing or stale identity and ACL state denies access.
- Source outages preserve the last valid canonical revision and block absence
  reconciliation.
- Parser failures preserve the prior active extraction.
- Semantic or reranker failure may fall back to lexical only when policy allows.
- Generator failure produces an extractive response or typed refusal according
  to policy, never an unsupported synthetic answer.
- Retries are limited to idempotent operations and respect deadlines.
- Logs and traces redact document contents, credentials, personal identifiers,
  and provider payloads according to policy.

External content is always untrusted evidence and is never executed as agent or
system instruction.

## Observability

OpenTelemetry-compatible traces and Prometheus-compatible metrics expose each
pipeline stage without requiring a specific hosted vendor. Traces include the
query plan, retrieval stages, candidate and selected evidence IDs, ACL policy
version, model revisions, warnings, latency, and usage. Full document bodies and
answers are not retained unless policy explicitly permits them.

Operational dashboards can be built by downstream systems from API status,
metrics, and traces. Alerts cover source failures, stale ACL snapshots, queue
age, extraction failure spikes, parser quarantine, projection lag, search and
answer latency, backup age, storage pressure, unapproved egress, and RLS test
failures.

## Evaluation and release gates

Quality is measured separately for:

- ingestion coverage and source immutability;
- parser and locator fidelity;
- retrieval recall, rank, latest-version selection, and hard negatives;
- evidence freshness and context precision and recall;
- answer correctness, completeness, groundedness, and citation precision and
  recall;
- refusal appropriateness;
- ontology entity, relation, graph path, temporal, and contradiction quality;
- ACL leakage, latency, cost, and resource use.

The full release gate requires at least 100 reviewed private Korean questions
plus a frozen holdout. Cases cover exact identifiers, paraphrases, tables,
stale revisions, near duplicates, denied distractors, cross-document questions,
ontology relations, contradictions, and appropriate refusals.

Production traces are telemetry, never training truth. A redacted trace may
enter a candidate queue. A human must approve the expected evidence, ACL,
answer, and rubric before it enters a versioned golden dataset.

Verification layers are unit, port contract, PostgreSQL integration, CLI/REST/
MCP parity, end-to-end, security, resilience, performance, setup, backup and
restore, upgrade, and rollback tests. Real containers and representative corpus
fixtures are preferred over broad mocks.

## Packaging and private handoff

Each versioned release bundle contains:

- Python wheel, CLI, and SDK;
- API, worker, and migration OCI images;
- digest-pinned Compose profiles;
- append-only migrations and ontology releases and migrations;
- JSON Schema, OpenAPI, and example connector contracts;
- portable retrieval, sync, ontology, and setup Skills;
- setup answer schemas and safe templates;
- SBOM, dependency, license, and secret scan results;
- checksums and build provenance;
- install, operations, upgrade, rollback, and recovery guides.

No private corpus, database, CAS, credential, or company path is included.

CI covers Python 3.12 and 3.13, PostgreSQL 18, minimal and optional dependency
profiles, type checking, Ruff, generated contracts, unit and integration tests,
image builds, and security scans. Optional remote adapters use wire-level
contract tests; production credentials are never required by CI.

## Manual acceptance

A release is accepted only after a blank environment completes this flow:

1. Ask an AI agent to set up KIP.
2. Answer the agent's missing setup questions.
3. Review and approve the generated setup plan.
4. Mount only approved source roots read-only.
5. Migrate and ingest the sample source.
6. Run search, answer, exact read, and XLSX deep-read through CLI and REST.
7. Verify the same allowed and denied principal behavior through every edge.
8. Create, review, approve, retrieve, explain, and cite an ontology assertion.
9. Exercise generator, source, parser, and database failure paths.
10. Restore the backup into a new database and CAS path.
11. Produce a redacted acceptance receipt with fingerprints and limitations.

Passing tests without this user-visible setup and retrieval flow is insufficient.
