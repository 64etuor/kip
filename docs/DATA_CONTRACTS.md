# Data Contracts

Application boundaries use canonical Pydantic models. CLI, REST, and MCP
serialize those models through versioned JSON envelopes, while the Python SDK
consumes the REST JSON contract. Static JSON Schema files are generated into
`contracts/`.

The stdio adapter uses MCP SDK 2.x protocol models, but every KIP tool result is
still a UTF-8 serialized `kip.envelope.v1` document. MCP protocol negotiation,
tool naming, and initialization metadata do not create a second KIP data
contract. The MCP `serverInfo.version` value is the KIP package version.

Completed MCP handlers return this envelope for domain validation failures as
well as success. Tool schemas advertise canonical bounds; malformed protocol
argument shapes rejected before a handler remain SDK errors. Unexpected handler
failures return `internal_error` without internal details. Mutation hints do
not replace authorization, and discovery approval describes its YAML writes.

## Answer evidence completeness

Shallow `xlsx_sheet` units are discovery metadata, not final-answer evidence,
regardless of question language. When no other admissible evidence remains,
`exact_xlsx_read_required` carries discovery citations with workbook/sheet
locators for a subsequent exact range read. A refusal's citations are not
claims that the missing values were read.

Partial CSV units qualify only with full contiguous, nonoverlapping row
coverage from the same artifact/extraction/hash and sufficient `max_chars` to
include the complete table. Otherwise `csv_full_table_required` identifies the
missing completeness/budget boundary. Missing legacy completeness metadata is
not assumed to mean a complete table. Checks run again after evidence filtering;
excluded table units cannot leave unsupported ontology context. Complete
non-table evidence can still answer a query that also matched a workbook.
See ADR-058.

## Public envelope

```json
{
  "schema_version": "kip.envelope.v1",
  "ok": true,
  "data": {},
  "error": null,
  "meta": {
    "request_id": "req_...",
    "workspace": "default",
    "generated_at": "2026-09-12T00:00:00Z",
    "warnings": []
  }
}
```

`meta` always carries all four fields. `schema_version` is always
`kip.envelope.v1`; `ok` and `meta` are the only required top-level members.

## Package archive manifest

`KIP-MANIFEST.json` validates against
`contracts/package-manifest.schema.json` and uses
`kip.package-archive.v1`. It records the KIP version, UTC creation time, the
single versioned ZIP root, a `sha256:` digest for every source payload file,
and source state (`git_commit`, `tracked_changes`, `repository`). Unknown
fields are rejected. `repository` is the https origin the archive was built
from, reduced to scheme, host, and path so that an access token in the
remote's userinfo and a local clone path never reach an archive handed to
another organization; it is `null` when no shareable http(s) remote exists.
Older `kip.starter-archive.v1` manifests omit the field and stay valid. That
legacy identifier (releases before 3.10.0) is still accepted by the schema and
by `PackageArchiveManifest`; archives built today always carry
`kip.package-archive.v1`.

The manifest intentionally excludes itself and `SHA256SUMS` from `files`.
`SHA256SUMS` covers every payload file and the manifest; the external
`<archive>.zip.sha256` covers the exact ZIP bytes. The build and verify commands
emit `kip.envelope.v1` containing a `kip.package-archive-receipt.v1` receipt
with archive path, archive digest, file count, root, status, and version.

## Guided setup boundary

`SetupAnswers` and `SetupPlan` use their existing v1 contracts with an additive
`relation_mining_mode` choice (`enabled` or `disabled`). New plans always carry
the explicit choice. Legacy plan payloads may omit it and retain the fail-closed
disabled behavior, while a resumed answer state is incomplete until the user
answers the new question. `enabled` is invalid when `model_provider=disabled`.

The generated container and host TOML files materialize the decision as
`models.relation_mining.enabled` together with the bounded unit, character,
entity-proposal, and relation-proposal caps. This enables candidate mining only;
the candidate, evidence, review, approval, and revocation contracts are
unchanged.

`SourceInventory` adds `local_file_count` and `cloud_placeholder_count` to the
metadata-only preview. `filesystem_sources` answers accept an absolute folder,
a JSON array of folders, or the existing explicit objects. Folder shorthand
derives a stable name and workspace ACL with `personal` classification for
personal ownership, otherwise `restricted`; preview and plan approval remain
required. `SetupPlan.runtime_uid` / `runtime_gid` and
`runtime_supplementary_gids` bind the non-root runtime identity and host group
memberships into the fingerprint. Legacy plans still deserialize but must be
regenerated before apply. Generated Compose is a standalone project, never an
override of the sample mounts. `runtime_readiness` checks resolved credentials,
key separation, local source availability, and reports an unprovisioned local
generation service as incomplete. The receipt starts with `app-up.sh`, which
orders database readiness and migration before services (ADR-057).

Guided `SourcePlan.target_root` equals its canonical `host_root`, preserving
shared host/container source URIs and snapshot identities. Old plans with split
source namespaces cannot apply; regenerate and explicitly sync the approved
scope. Managed CAS/backup paths remain runtime-specific.

## Search boundary

Filesystem scope is deployment configuration, not a new request filter.
Repository access combines verified ACLs with the current enabled source name,
ACL snapshot, and stored descendant path before ranking, limits, vocabulary,
and graph traversal. Removed or changed scope hides old records after reload;
exact reads additionally validate the live path and cloud residency. Public IDs
and versioned envelopes remain unchanged; callers cannot bypass this guard with
a known unit/artifact ID or a broader request ACL (ADR-056).

`SearchRequest` is the canonical application search request. Its versioned
fields are `query`, `limit`, optional `mode`, `source_kinds`,
`document_types`, `project_ids`, and `include_candidate_assertions`.
`mode` accepts `lexical`, `vector`, `hybrid`, or `reranked`; omitting it selects
the deployment default (`search.default_mode`, shipped as `hybrid` with
semantic search on since ADR-065; `reranked` is the value for deployments that
enable a cross-encoder). A request that omits `mode` degrades to the
lexical path when the model runtime or active projection is unavailable, while
an explicit vector-family `mode` fails instead of degrading. CLI, REST, MCP, and the Python SDK expose the same
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

`SearchHit.evidence_role` is always `discovery` and
`SearchHit.source_verification` is always `not_checked`: the hashes on a hit
describe the index, not a live source check. Both lexical and vector results,
in the Memory and PostgreSQL backends, carry one paragraph-bounded,
query-aware preview: the paragraph containing the most distinct query terms,
ties broken by source order, cut to a bounded window and NFC-normalized. The
preview is presentation only. It is neither content filtering nor an exact
quote, so a fact must still be reopened with `read` (or `xlsx-read` for
workbook values) before it is cited. Previews are computed from the full unit
body in the application layer, so the per-hit transfer from PostgreSQL is
bounded by how large a unit the parser produced. There is no
`extraction.max_chars_per_unit` key: the only configurable limit is
`max_chars_per_unit` under `[parsers.hwp.hwp-hwpx-parser]` (default 4000),
which bounds the chunk size of HWP/HWPX units at extraction time. Other
parsers apply the same 4000-character bound as a hard-coded default. When a search or context request
returns nothing and no indexed unit is visible to the caller, `meta.warnings`
carries `no_visible_indexed_units` on every edge; it describes the caller's
own scope and never asserts that hidden units exist. When the retrieval run
degraded, the search and context envelopes' `meta.warnings` list it on every
edge — alongside `no_visible_indexed_units` when the result is also empty,
because a degraded run that returned nothing carries no hit metadata:
`semantic_degraded` (default-mode search fell back to lexical because the
model runtime or active projection was unavailable), `rerank_degraded` (only
in a deployment whose default mode is `reranked`: only the reranker failed; the
fused lexical+vector ranking was kept), or
`lexical_rerank_degraded` (the lexical reranker failed or, in the semantic
fallback, is not configured; lexical order was kept). The same markers remain in hit metadata and redacted traces.
Two further markers use the same channel: `context_truncated` accompanies
`ContextBundle.truncated` (one event, one name) when a bundle was cut to the
requested budget, and `search_failed` rides the `ok: false` envelope of a
request whose retrieval raised, so a failed search is named and not only
traced.

Verification fields report how the source was checked when the unit was
reopened. `EvidenceRead.source_verification`,
`ContextItem.source_verification`, and `AnswerCitation.source_verification` are
one of `stat` (size and modification time still matched the indexed revision,
so no new digest was computed), `sha256` (the live file was hashed), or
`unavailable` (the source could not be read, in which case
`source_changed_since_index` is true). `ContextItem.body_truncated` is true
when the returned body is only the leading portion of the unit; a truncated
context item cannot show that something is absent. These fields are additive:
envelope versions are unchanged and readers must tolerate unknown fields.

## PDF evidence boundary

The configured PDF backend always emits one `pdf_page` per original 1-indexed
page. Under `pdf_inspector`, the body is structured Markdown and metadata marks
`source=pdf_inspector` plus whether the page needs OCR. The page locator remains
`{"page": N}` and is the exact-evidence boundary.

An additive `pdf_table` uses `page`, `end_page`, and `table_index`. Markdown
tables report `source=pdf_inspector`, `strategy=markdown`, rows, and columns but
no bbox. Selective bordered-table fallback reports
`source=pymupdf.find_tables`, `strategy=lines_strict`, and a bbox. Callers must
not assume every table source has geometry.

Extraction metadata records the selected backend, table/column pages, OCR
candidate reasons, Markdown table pages, and PyMuPDF fallback-page count. These
are provenance and quality signals, not authorization input.

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
  by settle, symlink, cloud-residency, filter, or size policy count as seen.
- `tombstoned`: objects whose consecutive absence reached
  `[sync] deletion_grace_scans` and were soft-deleted through the shared
  tombstone-revision path; prior revisions and approved assertions are
  preserved, and nothing in the source tree is written.

`absent` and `tombstoned` implement the filesystem deletion grace policy
(ADR-039); event connectors report `0` for both because deletions arrive as
explicit tombstone events. Counters are additive per run and are operational
telemetry, not evidence.

`SyncSummary` and `ReextractionSummary` (from `parser reextract --activate`)
carry an optional `semantic_projection` object (`SemanticProjectionUpdate`,
ADR-065) describing what the run did to the semantic projection. It is `null`
when semantic search is off or a re-extraction was not activated. Fields:

- `status`: `disabled` (semantic search or the embedding adapter is off),
  `current` (nothing new to embed), `updated` (new or changed units were
  embedded), `incomplete` (eligible units are still missing), or
  `unavailable` (the model runtime could not be reached; the sync itself does
  not fail and the next sync resumes).
- `space_id`, `newly_indexed_units`, `indexed_units`, `content_units`.
- `active` and `activated`: whether the space is active, and whether this run
  activated it. Only a complete space whose embedding identity the release
  reviewed activates automatically (`search.semantic_auto_activate`).
- `reason`: an optional explanation, also appended to `warnings` as
  `semantic projection: <reason>`.

CLI syncs additionally print `semantic projection: embedded N/M new or changed
units` progress on stderr; stdout stays the JSON envelope.

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
only fresh evidence reopened by the application service. `AnswerRefusalReason` is a closed vocabulary of exactly ten values:
`no_admissible_evidence`, `no_fresh_evidence`, `answer_not_present` (the
requested fact is absent from the reopened evidence),
`clarification_required` (unresolved short multi-document ambiguity),
`exact_xlsx_read_required`, `csv_full_table_required`,
`insufficient_decision_evidence`, `model_egress_denied`,
`generation_unavailable` (the configured provider could not be reached), and
`generation_invalid` (the generator returned citations that do not validate).
`contracts/answer-response.schema.json` and `contracts/query-trace.schema.json`
mirror the same ten.

When the entire `answer` query equals the basename of a `file://` source, the
request is treated as a request for that document's extracts and is answered
with citations instead of the body-relevance refusal. If more than one allowed
file with that exact name has differing content, the answer refuses with
`clarification_required`; identical copies count as one document. The
ambiguity check runs over the ACL-visible corpus before the result limit is
applied, so `limit=1` cannot hide a conflicting file. A filename embedded in a
longer factual question (`보고서.txt 제출기한은?`) scopes the evidence to that
document without relaxing adequacy: the remaining question must be present in
it, otherwise the answer refuses with `answer_not_present`, and the same
ambiguity rule applies. A shallow XLSX extract still returns
`exact_xlsx_read_required`.
Filename binding is resolved before ranking, result limits and live freshness
checks (ADR-060). Balanced straight/curly quotes and backticks use the same
parser as inclusion, exclusion and ambiguity. Missing or inaccessible named
evidence returns `no_admissible_evidence`; changed named evidence may return
`no_fresh_evidence`. Neither condition permits an answer from other files.
Explicit inclusion/exclusion also constrains approved ontology evidence.
The internal filename criteria are not new public `SearchRequest` fields.
Every positively named file must contribute usable fresh evidence; a tight
limit or one stale named file cannot silently turn a multi-file request into
an answer about only the remaining file. Ontology evidence in a file-scoped
answer must be among the eligible retrieved units as well.
Extractive fallback is visible through `retrieval_mode` and `warnings`; it is
the primary local behavior when structured generation is disabled. When
generation is enabled, a generator failure falls back only when
`models.generation.fallback_on_error=true`; otherwise it returns a typed
refusal. With generation disabled, `kip answer` makes no remote generator call.

A question that names a file with an extension the deployment indexes
(`include_extensions` plus the built-in document list) refuses with
`no_admissible_evidence`, naming that file, when no allowed evidence matches;
URLs (`scheme://` or `www.`) in the question are context rather than file
requests. A question that only excludes a file refuses with
`clarification_required`. Basenames are recognized from quoted spans or an
extension token with up to nine preceding words; names outside that shape
still bind through a containment fallback, and parentheses or brackets inside
or after a name are allowed.

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
for inspection and retention pruning. No trace exporter ships: `QueryTraceExporter`
remains an extension seam with no adapter behind it, so a deployment that adds one
owns the redaction budget it forwards.

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
