# Application Integration

Named-file `answer` requests resolve scope before ranking or freshness checks.
Use balanced quotes for names with spaces, for example
`"사업 보고서.pdf" 제출기한은 언제인가?`. Unknown/inaccessible or stale named
evidence returns a typed refusal instead of borrowing another file's answer.
Inclusion/exclusion also applies to ontology evidence. No new public request
fields are required; CLI, REST and MCP share this behavior (ADR-060).

## Preferred integration order

1. REST/OpenAPI for web and backend applications.
2. Python SDK wrapper for scripts and services.
3. MCP for AI clients that support tools.
4. CLI subprocess for local automation and agents.

CLI and MCP call the focused application use cases directly, REST exposes those
same use cases over HTTP, and the Python SDK delegates to REST. The SDK does not
contain ranking, ACL, or review logic.

Search uses one `SearchRequest` across CLI, REST, MCP, and the Python SDK:
`query`, `limit`, optional `mode`, source kinds, document types, project IDs,
and candidate-assertion inclusion. Missing fields use canonical defaults, and
every edge delegates ranking, ACL, and refusal behavior to the shared
application services. See `DATA_CONTRACTS.md` for the exact wire contract.

3.7.0 adds fields to existing envelopes without changing their versions:
`evidence_role` and `source_verification` on `SearchHit`, `source_verification`
on `EvidenceRead`, `ContextItem`, and `AnswerCitation`, and `body_truncated` on
`ContextItem`. Integrators must tolerate unknown fields rather than reject
them. Treat every hit with `evidence_role=discovery` (and its
`source_verification=not_checked`) as a pointer, not evidence: reopen the unit
before quoting or citing it.

3.12.0 turns semantic search on by default (ADR-065) without changing envelope
versions: the default mode is `hybrid`, and the BGE cross-encoder reranker is
opt-in. Omit `mode` to get the deployment default; it degrades to lexical while
the model runtime or projection is unavailable, and reports every degradation
in the search and context `meta.warnings`.
[`DATA_CONTRACTS.md`](DATA_CONTRACTS.md) holds the canonical list of warning
codes and their meanings; treat that list as the contract rather than the
subset a client happens to have seen. Surface those warnings rather than
presenting the result as semantic. An explicit `vector`, `hybrid`, or
`reranked` mode fails instead of degrading. Inline sync and re-extraction
summaries gain an optional `semantic_projection` object. Check
`capabilities.semantic_search` before offering a semantic-only path.

Allowed filesystem directories come from the deployment's enabled source
configuration, not REST/SDK request fields. This policy also hides previously
indexed records after a source is removed or changed and the service reloads.
Known unit/artifact IDs and broader caller ACLs cannot expand it. Exact reads
recheck live paths; cloud-only bytes can leave cached text stale, and live XLSX
reads fail until the operator makes the selected file locally available.

## Health probes

- `GET /healthz` (process liveness only; no authentication)
- `GET /readyz` (liveness plus a canonical-store round-trip; fails closed with
  503 on repository failure)

## Read API

- `GET /v1/capabilities`
- `GET /v1/status`
- `POST /v1/search`
- `POST /v1/context`
- `POST /v1/answer`
- `GET /v1/vocabulary` (required `prefix`; optional `limit`)
- `GET /v1/units/{unit_id}`
- `GET /v1/artifacts/{artifact_id}`
- `GET /v1/documents/{document_id}`
- `GET /v1/assertions/{assertion_id}`
- `GET /v1/assertions/{assertion_id}/explain`
- `GET /v1/xlsx/{artifact_id}/range` (required `sheet` and `cell_range`;
  optional `allow_stale=true`)
- `POST /v1/graph/neighbors`
- `POST /v1/graph/path`
- `POST /v1/ontology/context` (approved ontology context for a query;
  `include_candidate_assertions` opts into labeled candidates)

## Interaction API

Interaction endpoints run under the authenticated caller's principal scope.

- `POST /v1/interactions/clarifications`
- `GET /v1/interactions/clarifications/{question_id}`
- `POST /v1/interactions/clarifications/{question_id}/answers`
  (`question_id` in the payload must match the path)
- `GET /v1/interactions/preferences`
- `PUT /v1/interactions/preferences`
- `DELETE /v1/interactions/preferences/{key}`
- `POST /v1/interactions/feedback`
- `POST /v1/ontology/discovery-candidates` (propose a staged ontology
  discovery candidate; optional spec fields `parent`, `domain`, `range`,
  `inverse`, `risk`, `review`, `extraction` refine the eventual release;
  review stays admin-only)

## Write/admin API

- `POST /v1/connectors/events`
- `POST /v1/sync/{source_name}`
- `POST /v1/sync/filesystem/{source_name}` (enqueues a job by default;
  optional `enqueue=false` for an inline run and `dry_run=true`)
- `GET /v1/jobs` (optional `status` and `limit` filters)
- `GET /v1/admin/query-traces` (optional `request_id` and `limit` filters)
- `DELETE /v1/admin/query-traces/expired`
- `DELETE /v1/admin/interactions/clarifications/expired`
- `GET /v1/ontology/entities` (optional `limit`, default 100)
- `POST /v1/ontology/entities`
- `POST /v1/ontology/mining-jobs` (enqueues a reviewed mining job over
  `unit_ids`)
- `GET /v1/admin/ontology/discovery-candidates` (optional `status`, default
  `proposed`; optional `limit`, default 100)
- `POST /v1/admin/ontology/discovery-candidates/{candidate_id}/review`
  (approving an `entity_type`/`predicate` candidate materializes an additive
  ontology release and returns a `release` object with the file, bumped
  version, and `catalog_refresh` — `"restart_required"` for the long-running
  API/worker/MCP processes, immediate for fresh CLI invocations)
- `GET /v1/ontology/entity-candidates` (optional `status`, default `proposed`;
  optional `limit`, default 100)
- `GET /v1/ontology/entity-candidates/{candidate_id}`
- `POST /v1/ontology/entity-candidates/{candidate_id}/approve` (optional `note`)
- `POST /v1/ontology/entity-candidates/{candidate_id}/reject` (optional `note`)
- `GET /v1/review/candidates` (returns `kip.assertion-candidate-listing.v1`;
  optional `status`, default `proposed`; optional `limit`, default 100;
  optional `predicate` and `subject_id` filters)
- `POST /v1/review/candidates` (records a human-origin assertion candidate,
  mirroring CLI `review propose`; the candidate enters the same review queue
  and is never auto-approved)
- `GET /v1/review/candidates/{candidate_id}`
- `POST /v1/review/candidates/{candidate_id}/approve`
  (optional `supersede_contradicted=true`)
- `POST /v1/review/candidates/{candidate_id}/reject`
- `POST /v1/review/assertions/{assertion_id}/revoke` (required `note`)

## MCP tool surface

Each tool declares its purpose and read/mutation hints. Domain validation
failures retain `kip.envelope.v1`; schemas advertise canonical numeric bounds,
and unexpected handler failures return a generic `internal_error`. Hints are
guidance, not permissions. `kip_answer` can refuse with workbook/sheet citations
so a client can call `kip_xlsx_read`. Native-client observations and connection
limits are recorded in [agent quality](AGENT_QUALITY.md).

The stdio MCP adapter (`src/kip/mcp_server.py`) exposes these tools, each a
pure delegation to the same application services as CLI and REST:

It runs on the stable MCP 2.x SDK, reports the KIP package version during
initialization, and supports the current protocol plus SDK-negotiated legacy
clients. Tool payloads remain `kip.envelope.v1`; protocol metadata and client
capabilities never grant a principal, workspace, scope, or role.

- Read/answer: `kip_capabilities`, `kip_status`, `kip_search`,
  `kip_vocabulary`, `kip_context`, `kip_answer`, `kip_read`, `kip_xlsx_read`,
  `kip_explain_assertion`
- Graph and ontology context: `kip_graph_neighbors`, `kip_graph_path`,
  `kip_ontology_entities`, `kip_ontology_context`
- Ontology curation: `kip_ontology_mine`, `kip_ontology_candidates`,
  `kip_ontology_entity_create`, `kip_ontology_entity_candidate_approve`,
  `kip_ontology_entity_candidate_reject`,
  `kip_ontology_relation_candidate_approve`,
  `kip_ontology_relation_candidate_reject`, `kip_ontology_assertion_revoke`,
  `kip_ontology_discovery_propose`, `kip_ontology_discovery_candidates`,
  `kip_ontology_discovery_review`
- Interaction memory: `kip_clarify`, `kip_answer_clarification`,
  `kip_preferences`, `kip_remember_preference`, `kip_forget_preference`,
  `kip_feedback`
- Operations visibility: `kip_jobs`

Deliberate scope boundary: MCP does not expose sync triggers, telemetry,
projection maintenance, raw get-by-id record reads, or manual assertion
candidate creation. Synchronization and rebuilds must never be triggered from
a normal retrieval surface (`AGENTS.md`: "Ordinary retrieval does not
authorize sync, re-index, or projection rebuilds"); use the CLI or the admin
REST routes for those operations.

## Trusted identity

```text
API-key bootstrap: X-KIP-API-Key: <service key>
JWT deployment:    Authorization: Bearer <verified organization token>
Optional tracing:  X-Request-ID: <opaque request id>
```

Production deployments should place the API behind an organization-approved identity-aware proxy. Do not expose PostgreSQL directly to applications.

The starter API separates ordinary reads from administrative writes:

- API-key mode derives the one configured principal, workspace, and ACL scopes
  from server configuration; callers cannot choose them.
- JWT mode verifies issuer, audience, signature, expiry, workspace, groups, ACL
  scopes, and dynamic ACL snapshot claims before application services run.
- In API-key mode, connector ingestion, source synchronization, and review
  operations additionally require `X-KIP-Admin-Key`.
- JWT administration derives the admin role from a configured trusted group and
  does not use the bootstrap admin key.
- Production rejects `X-KIP-Workspace`, `X-KIP-Principal`, and
  `X-KIP-ACL-Scopes`. Never build a proxy that forwards these as caller input.

## Custom connector event

Applications that already own a source integration should normalize each immutable change into `ConnectorEvent` rather than writing database rows.

```json
{
  "schema_version": "kip.connector-event.v1",
  "event_id": "evt_custom_001",
  "connector_name": "custom-crm",
  "operation": "upsert",
  "external_id": "message-123",
  "payload": {
    "source_kind": "crm",
    "subject": "A과제 협약 변경",
    "text": "협약 변경 신청서를 제출했습니다."
  },
  "acl_scopes": ["workspace:default", "project:A"]
}
```

Use a stable `event_id` and `external_id`. A repeated identical event is safe: the application service derives the same immutable revision and returns an unchanged result.

## Integration boundary

REST, MCP, the CLI, and connector events are edge adapters. They must not
implement ranking, ACL policy, ontology promotion, or parser decisions. Add a
new direct edge by composing the focused use cases in
`src/kip/application/runtime.py`; add a remote client through the versioned
REST/OpenAPI contract. Preserve the checked-in contracts under `contracts/`.
