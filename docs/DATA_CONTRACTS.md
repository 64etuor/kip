# Data Contracts

Application boundaries use canonical Pydantic models. CLI, REST, and MCP
serialize those models through versioned JSON envelopes, while the Python SDK
consumes the REST JSON contract. Static JSON Schema files are generated into
`contracts/`.

## Public envelope

```json
{
  "schema_version": "kip.envelope.v1",
  "ok": true,
  "data": {},
  "error": null,
  "meta": {
    "request_id": "req_...",
    "workspace": "default"
  }
}
```

## Search boundary

`SearchRequest` is the canonical application search request. Its versioned
fields are `query`, `limit`, optional `mode`, `source_kinds`,
`document_types`, `project_ids`, and `include_candidate_assertions`.
`mode` accepts `lexical`, `vector`, `hybrid`, or `reranked`; omitting it selects
the deployment default. CLI, REST, MCP, and the Python SDK expose the same
fields and call the same application service. Omitted optional filters retain
the canonical model defaults, and SDK payloads omit those defaults rather than
inventing a second wire contract.

Workspace, principal, roles, and ACL scopes belong to the verified internal
`RequestContext`; an API search payload cannot choose them. Local CLI
authorization flags are operator-test inputs, not part of `SearchRequest`.
When a CLI ACL option is explicitly present, including an explicitly empty
plural value, it replaces ambient `KIP_ACL_SCOPES`; when no option is present,
the configured environment value is used. This prevents a negative-scope test
from accidentally inheriting an ambient grant.

Public v1 does not yet carry a date range, entity filter, graph-expansion plan,
or cursor. Adding any such planner field requires a compatible generated-schema
change and parity updates across every edge; an edge-only planner option would
violate the contract.

`SearchHit` exposes `unit_id`, optional `document_id`, `artifact_id`,
`source_kind`, title, snippet, scalar score, reproducible locator, source URI,
indexed source hash, optional source modification time, and metadata.
Channel ranks, `is_latest`, diversity backfill, and degradation markers live in
metadata. Array order is result rank. The snippet and score remain discovery
data, never final evidence.

## XLSX exact-range boundary

`XlsxRangeRead.cells` is a rectangular matrix of the generated `XlsxCell`
contract. The matrix always has the same rows, columns, and coordinates as the
requested range, including blank cells beyond the workbook's used range. A
request must run from top-left to bottom-right, remain within `XFD1048576`, and
contain at most 100,000 cells.

Each cell carries JSON-only `value` and `cached_value` fields plus independent
`value_type` and `cached_value_type` markers. Date, datetime, and time values
use ISO 8601 strings; durations use ISO 8601 duration strings. Their
`excel_serial`/`cached_excel_serial` values and `number_format` preserve the
workbook representation needed for exact interpretation. Non-finite numeric
tokens use `NaN`, `Infinity`, or `-Infinity` strings with the
`non_finite_number` marker so a JSON serializer cannot silently turn evidence
into `null`.

Formula source stays separate from cached output through `formula`,
`formula_kind`, `formula_ref`, and JSON-safe `formula_attributes`; normal,
array, and data-table formulas are represented without leaking Python object
reprs. `display_value` is best-effort display text, not a replacement for
`value`, `number_format`, or the cached-value freshness caveat. Per-cell layout
metadata includes hidden/filtered row state, hidden column state, and merged
range/master identity. A filtered row means an OOXML-hidden data row inside the
worksheet AutoFilter range; OOXML does not record a stronger causal label.

## Canonical source sequence

```text
SourceObject -> SourceRevision -> Artifact -> ExtractionRun -> ContentUnit
```

`ContentUnit.lexical_text` is the versioned analyzer output used to rebuild the
lexical projection. It is stored with the extraction because normalized body
text alone cannot reproduce Korean n-grams, title tokens, and stable source
identifiers. The `search.lexical_units` row remains a disposable projection.

## Internal parser-worker boundary

Filesystem parser isolation uses a private, versioned file contract rather
than stdout capture. `kip.parser-request.v1` carries the stable parser key,
source path, project root, parser configuration, artifact/document IDs, and
ACL scopes. `kip.parser-response.v1` is either a successful canonical
`ExtractionRun` plus `ContentUnit[]`, or a bounded failure with one of
`configuration_error`, `internal_error`, `memory_limit`, or `parser_error`.

Both models reject unknown fields. The parent validates response bytes before
acceptance and caps the response file before reading it. This is an internal
adapter contract: CLI, REST, MCP, generated schemas, and the `ParserPort`
signature are unchanged.

## PPTX evidence boundary

PPTX extraction emits `pptx_text`, `pptx_table`, `pptx_chart`, `pptx_image`,
`pptx_notes`, `pptx_comment`, `pptx_diagram`, and optional `pptx_ocr` units. Shape locators use
`type=pptx_shape` with a one-based slide number, source slide and shape IDs,
nested group IDs, and `bbox_emu`. Notes use `pptx_notes`; comments use
`pptx_comment`; SmartArt data uses `pptx_part` with the package part path.

All unit and extraction metadata is `JsonObject`. OOXML timestamps cross the
boundary as ISO 8601 strings, images as hashes and metadata rather than bytes,
and chart values as JSON numbers or null. Hidden-slide state, source z-order,
derived reading order, merge ownership, text runs, and optional-part warnings
remain explicit. External relationship targets are recorded only as existing
run hyperlinks or aggregate counts and are never fetched.

`pptx_ocr` locators use `type=pptx_ocr` and carry the source slide, slide ID,
shape ID, nested group path, EMU geometry, and Kordoc pixel bounding box. Their
metadata carries the source image SHA-256, OCR adapter, block type, and
JSON-safe structured block fields. Identical picture bytes are recognized once
but produce one unit per source shape occurrence so every citation reopens the
right slide object.

PDF OCR preserves every native `pdf_page` unit and appends `pdf_ocr` units only
for quality candidates. A `pdf_ocr` locator contains the one-based page and
pixel bounding box. Extraction metadata exposes candidate reasons, adapter and
version, OCR block/page counts, and post-OCR text coverage. OCR image-reference
blocks and empty bodies do not cross the evidence boundary.

Command-parser blocks such as Kordoc output preserve table/image/list/span/link,
footnote, style, and child fields in `ContentUnit.metadata`. Deterministic table
text and image placeholders make non-paragraph blocks searchable without
discarding their structured payload. Structured parser warnings become stable
strings containing code, optional page, and message.

`SourceObject.acl_snapshot` records the source ACL provider, version, captured
time, expiry, and canonical scopes. `ContentUnit.acl_snapshot_id` pins each
projection row to that snapshot. Configuration-owned snapshots may be
non-expiring; connector-derived snapshots must expire. Repository queries deny
rows whose dynamic snapshot is stale.

`SourceObject.classification` and `ContentUnit.classification` carry the same
typed canonical value. Ingestion derives it from approved source configuration,
checks source/unit equality, and persists it in PostgreSQL. Existing rows are
backfilled as `restricted`; a subsequent approved source sync may reclassify
them. Model egress never accepts a classification supplied with an answer
request.

`EgressDecision` contains the destination, redacted policy fingerprint,
admitted evidence IDs, denied evidence IDs, and a typed denial reason.
`allowed=false` prohibits a generator call even when an admitted subset is
non-empty.

## Sync summary boundary

`SyncSummary` is the read model an inline synchronization run returns through
the standard envelope (`kip sync run` / `kip sync all` without `--enqueue`,
and REST `POST /v1/sync/filesystem/{source}` with `enqueue=false`); enqueued
sync runs return a `job_id` instead. It carries the `source` name plus per-run
counters and bounded `warnings` strings:

- `scanned`: source files or connector events the run observed.
- `inserted` / `replaced` / `unchanged`: ingest outcomes per object; a repeated
  identical revision is `unchanged`, never a duplicate row.
- `failed`: objects whose ingest raised, each with a recorded warning.
- `skipped`: objects observed but not ingested (dry-run scans).
- `absent`: active indexed objects a complete filesystem scan did not see;
  each increments that object's consecutive-absence counter. Only a complete,
  successful, non-dry-run scan contributes absence evidence, and a scan that
  sees zero files skips reconciliation with a warning instead of marking
  anything. Directory walk errors make a scan incomplete, while paths deferred
  by settle, symlink, filter, or size policy count as seen.
- `tombstoned`: objects whose consecutive absence reached
  `[sync] deletion_grace_scans` and were soft-deleted through the shared
  tombstone-revision path; prior revisions and approved assertions are
  preserved, and nothing in the source tree is written.

`absent` and `tombstoned` implement the filesystem deletion grace policy
(ADR-039); event connectors report `0` for both because deletions arrive as
explicit tombstone events. Counters are additive per run and are operational
telemetry, not evidence.

## Generated answer boundary

`GenerationRequest` contains a query plus bounded exact evidence bodies,
reproducible locators, public content-unit IDs, and optional
`GenerationRelation` records. A generation relation is an already-approved,
currently valid assertion whose complete evidence IDs are present in the same
request; it is context rather than independent evidence. `GenerationResult` contains
typed claims, the configured provider/model/revision, token usage, and a
provider request ID. A supported claim must cite evidence, evidence IDs must be
unique, and all returned IDs must be a subset of the request.

`AnswerResponse` remains `kip.answer.v1` and adds structured claims, generation
metadata, and the applied `EgressDecision`. A successful generated answer cites
only fresh evidence reopened by the application service. Typed refusals cover
no admissible or fresh evidence, requested facts absent from the reopened
evidence (`answer_not_present`), unresolved short multi-document ambiguity
(`clarification_required`), exact XLSX-read requirements, egress denial,
provider unavailability, and invalid generated citations.
Extractive fallback is visible through `retrieval_mode` and `warnings`; it is
the primary local behavior when structured generation is disabled. When
generation is enabled, a generator failure falls back only when
`models.generation.fallback_on_error=true`; otherwise it returns a typed
refusal. With generation disabled, `kip answer` makes no remote generator call.

## Knowledge sequence

```text
EntityCandidate -> Entity
RelationProposal -> AssertionCandidate -> ApprovedAssertion -> AssertionEvidence
```

`KnowledgeEntity` stores an ontology type, normalized canonical name, aliases,
status, and ACL scopes. `RelationProposal` names exact evidence-unit IDs and a
versioned derivation. The application reopens those units and materializes
`CandidateEvidence` with source revision hash, locator, and quote hash.

`RelationMiningRequest` contains exact evidence bodies, visible existing
entities, the active ontology version, and bounded proposal counts.
`RelationMiningResult` contains typed entity and relation proposals plus a
pinned model revision, token usage, and per-proposal `skipped` records
(`MinedProposalSkip`: kind, reference, reason). Unknown types, predicates,
entity IDs, evidence IDs, domain/range violations, duplicates, and malformed
intervals are skipped with a recorded reason instead of failing the whole
batch; batch-level contract breaches (wrong ontology version, malformed
output shape, proposal counts over the configured limits, model revision
mismatch) still fail closed. Stale evidence units are excluded per unit and
reported as `evidence_unit` skips — a stale unit is never silently mined.
Source text remains a data field and cannot alter the system instruction.

`OntologyMiningSummary` (`kip.ontology-mining.v1`) additively carries the
same `skipped` list. When mining runs as a durable job, the summary IDs and
skip reasons are recorded into the job payload under `result`
(`kip.ontology-mining-result.v1`) so `kip jobs list`, REST `/v1/jobs`, and
the MCP `kip_jobs` tool can surface them; `JobRecord` additively exposes
`last_error` for failed runs. The mining job idempotency digest includes a
hash of the caller-visible approved entity set, so approving entity
candidates makes a re-mine of the same units a new job (the mine ->
approve entities -> mine again loop) instead of deduplicating onto the
finished one.

Model-discovered entities first become `EntityCandidate` records with their own
stable `ecand_` IDs and exact evidence. Human approval creates a separate
`KnowledgeEntity` with evidence-derived ACL scopes; rejection never creates an
entity. Relation and entity candidates are both excluded from graph traversal
and answer evidence until their independent approvals finish.

Candidate fingerprints cover ontology version, normalized subject/object
identity, predicate, validity interval, exact source revision and locator, and
miner revision. Repeating the same proposal returns the same public candidate
ID. A changed source or miner revision creates a different review candidate.
Overlapping active assertions with a different object are recorded as explicit
contradictions; no candidate is silently promoted or used as a fact.

A candidate's `review_risk` is normalized from the active catalog at
proposal time, and approval derives the evidence requirement from the loaded
ontology (`review == "required"` or `risk == "high"`), never from a
hardcoded predicate list; stores keep a fail-closed floor pinned to
`ontology/core/predicates.yaml` by a contract test.

## Candidate review listing

`AssertionCandidateListing` is `kip.assertion-candidate-listing.v1`, the read
model returned by `kip review list`, REST `GET /v1/review/candidates`, and
the `relations` section of `kip ontology candidates` / MCP
`kip_ontology_candidates` (which additively adds `relations_total`). Each
item is an `AssertionCandidateView`: all `AssertionCandidate` fields plus
additive review aids — `subject_display_name`, `object_display_name`,
`predicate_label_ko`, `predicate_description`, and `evidence_previews`
(`CandidateEvidencePreview`: content unit ID, `readable`, title, bounded
snippet). Previews are resolved with the caller's own ACL context: a snippet
is included only when the requesting principal can already read the unit,
and snippets remain discovery aids, never final evidence. Items are ordered
by review risk (high first), then confidence (high first, unknown last).
The listing carries `total` plus the applied `status`, optional `predicate`,
and optional `subject_id` filters.

## Assertion review lifecycle

`ApprovedAssertion.status` transitions are append-style and auditable:

- `active -> revoked` via the application revocation service
  (`kip review revoke`, REST `POST /v1/review/assertions/{id}/revoke`, MCP
  `kip_ontology_assertion_revoke`). A non-empty note is required;
  `revoked_at`, `revoked_by`, and `revocation_note` are recorded additively
  on the assertion. Revoked assertions are excluded from every
  approved-only consumption path (graph neighbors/paths with
  `approved_only`, ontology answer context, contradiction checks, and
  active-assertion listings) but remain readable by ID for audit.
- `active -> superseded` when a reviewer approves a candidate carrying
  `contradicts_assertion_ids` with `supersede_contradicted=true`; the
  contradicted assertions record `superseded_by` referencing the new
  assertion in the same transaction. Supersede targets must be among the
  candidate's recorded contradictions.

`AssertionExplanation` is a read model that combines one approved assertion with the exact `EvidenceRead` units supporting it. It is not stored as a second source of truth.

`OntologyAnswerContext` is a versioned read model containing ACL-visible matched
entities, current approved edges, bounded paths, and their exact evidence-unit
IDs. The application removes candidates, non-active assertions, future or
expired validity intervals, inaccessible evidence, and source-changed evidence
before constructing it. An answer exposes the context only with citations for
all included graph evidence.

`include_candidate_assertions=true` additively populates
`OntologyAnswerContext.candidates` with `OntologyAnswerCandidate` records on
the surfaces that carry an ontology context section (`kip answer`,
`kip ontology context`, REST `/v1/answer` and `/v1/ontology/context`, MCP
`kip_answer` and `kip_ontology_context`). Candidate entries are always
labeled `status="proposed"`, are kept separate from approved `edges`, never
join `evidence_unit_ids`, never feed generation relations or citation
requirements, and are subject to the same evidence-visibility ACL gating as
candidate listings. On `search` and `context` responses, which have no
ontology section, the flag is recorded in telemetry only.

`ApprovedAssertion.evidence_acl_snapshot_ids` is the denormalized freshness
guard for its reviewed evidence. It is rebuilt from canonical evidence and does
not replace `AssertionEvidence` or its exact locator.

## Ontology migration boundary

`OntologyMigration` maps source-release symbols to target-release symbols with
strict rename, replace, split, merge, or deprecate arity. Materialization scans
only ACL-visible active assertions at the declared source version. Every target
candidate preserves exact evidence, validity, a manifest SHA-256, and explicit
`migrates_assertion_ids` lineage. Repeating the same manifest returns the same
candidate IDs and never rewrites or supersedes the approved source assertion.
Target-version approval is rejected until that ontology release is the active
application contract. Normal graph traversal is likewise pinned to the active
catalog version, preventing reviewed future-release assertions from leaking
into current answers.

`OntologyMigrationMaterialization` reports source, created, existing, and
deprecated counts in `kip.ontology-migration-materialization.v1`. A manifest
affecting existing assertions must require review. Live entity-type migrations
fail closed because changing an entity identity or type without merge/split
history would make target assertions semantically invalid.

## Query trace boundary

`QueryTrace` is `kip.query-trace.v1`, a closed redacted operational contract.
It records route, outcome, bounded filter counts, retrieval stages, public
candidate IDs and ranks, selected evidence IDs, ontology assertion IDs, ACL
policy version, pinned model revisions, token totals, warning codes, refusal
reason, and latency. It has no fields for query text, source bodies, snippets,
answers, principals, credentials, provider request IDs, or raw provider
payloads. Caller-supplied request IDs are omitted unless they match KIP's opaque
server-generated ID format.

PostgreSQL is canonical for query traces in production. Workspace RLS applies
on write and read, while the application additionally requires an admin role
for inspection and retention pruning. OTel spans and metrics receive only
bounded route/outcome/count/latency attributes, not candidate or evidence IDs.

## Adaptive interaction boundary

`ClarificationQuestion` is `kip.clarification.v1`: it has a bounded prompt,
at most eight stable choice IDs (`CLARIFICATION_CHOICE_CAP`, a
human-readability bound), a caller-only scope, and an expiry. Its answer
is transient unless `remember=true` is accepted for a question carrying a
preference key. Direct `UserPreferenceWrite` records require `confirmed=true`.
`UserPreference` is `kip.user-preference.v1` and is visible only to its owning
principal.

`InteractionFeedback` is `kip.interaction-feedback.v1`. It records only an
opaque KIP request ID when available, a controlled outcome, and controlled
reason codes. It cannot carry query text, answer text, source content,
principal data, or a free-form comment and is not a `QueryTrace` extension.

`OntologyDiscoveryCandidate` is
`kip.ontology-discovery-candidate.v1`. An explicitly confirmed proposal is a
review item scoped to a YAML domain profile, never an active entity, predicate,
controlled value, alias, assertion, or graph edge. Reviewer outcomes are
`accepted_for_release` and `rejected`; only a separately reviewed ontology
release can change the active catalog. Candidate fingerprints are storage-only
and are excluded from edge output.

## Identity boundary

`RequestContext` contains a verified principal, workspace, scopes, roles, and
the principal ACL snapshot when applicable. It is application-internal request
state, not caller-authoritative JSON. API adapters construct it through the
configured identity port after cryptographic or constant-time credential
verification.

## Connector boundary

Custom applications submit `ConnectorEvent` records. The application layer converts the event into the same canonical source sequence used by built-in NAS, Slack, and mail connectors. Connectors never write source, content, search, or knowledge tables directly.

## Stability rules

- Public IDs are strings with stable prefixes.
- Unknown source metadata belongs in `metadata`, not ad-hoc top-level fields.
- Breaking fields require a new schema version.
- Adapter-specific fields must not leak into domain models.
- Evidence locators must be source-reproducible.
