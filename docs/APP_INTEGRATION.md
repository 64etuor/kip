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

3.14.0 makes source freshness three-valued (ADR-066) without changing envelope
versions: it adds `source_verification` to `XlsxRangeRead` and lets
`source_changed_since_index` be `true`, `false`, or `null` on `EvidenceRead` and
`ContextItem`, where `null` means the source could not be read and nothing was
compared. This is not purely additive for readers. A client that treated the
field as a plain boolean now sees `null` where it used to see `true`: a deleted
or unreadable source that used to raise a stale-source warning raises none
unless the client reads `source_verification` and treats `unavailable` as
unverified. Test `is not False`, never truthiness. `capabilities` now repeats
its `data.warnings` in `meta.warnings` on every edge, which is where every
instruction says to look, and so does `answer`: a successful answer can now
carry `generation_unavailable_extractive_fallback` or
`generation_invalid_extractive_fallback` there, which marks an extractive
answer produced after the generator failed rather than an anomaly. In the
other direction, `search_failed` no longer rides a failure whose `error.code`
is `validation_error`, `forbidden`, `not_found` or `configuration_error`, so a
client counting that marker to alert on search failures should count
`ok: false` responses instead.

Allowed filesystem directories come from the deployment's enabled source
configuration, not REST/SDK request fields. This policy also hides previously
indexed records after a source is removed or changed and the service reloads.
Known unit/artifact IDs and broader caller ACLs cannot expand it. Exact reads
recheck live paths; cloud-only bytes leave cached text unverified
(`source_verification=unavailable`, `source_changed_since_index=null`, not
"changed"), and live XLSX reads fail until the operator makes the selected file
locally available.

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
- Operations visibility: `kip_jobs`, `kip_doctor`

`kip_doctor` is read-only diagnostics (`checks[].details.reason` and
`details.fix`, plus `summary` / `summary_en`). It does not sync or rebuild.

Deliberate scope boundary: MCP does not expose sync triggers, telemetry,
projection maintenance, raw get-by-id record reads, or manual assertion
candidate creation. Synchronization and rebuilds must never be triggered from
a normal retrieval surface (`AGENTS.md`: "Ordinary retrieval does not
authorize sync, re-index, or projection rebuilds"); use the CLI or the admin
REST routes for those operations. Ordinary retrieval also does not authorize
sync because a query missed.

## Registering KIP with an MCP client

MCP gives a client the tools. The agent skills that teach an agent to use them
(`knowledge-fabric`, `kip-setup`) are installed separately with
`./scripts/install-agent-files.sh [personal|project DIR] [--client claude|codex|all]`;
installed copies record their deployment and are refreshed by `kip update`
(see [`OPERATIONS.md`](OPERATIONS.md)).

An MCP client starts the stdio server from its own working directory, not from
the KIP deployment, so a registration must not depend on that directory. Two
forms work from anywhere:

- **Launcher: `kip mcp`.** `scripts/install.sh` writes a `kip` launcher
  (default `~/.local/bin/kip`) that opens the deployment it installed, or
  `$KIP_HOME` when set. `kip mcp` serves the same tools as `scripts/mcp.sh`.
  The client resolves `kip` on its own `PATH`; if it cannot, register the
  launcher's absolute path in place of `kip`.
- **Absolute path**, for a checkout without the launcher:
  `bash /srv/kip/scripts/mcp.sh` (or `bash /srv/kip/scripts/kip mcp`). Both
  load the deployment's `.env` and select `config/kip.host.generated.toml` when
  setup generated it, otherwise `config/kip.toml`, so `KIP_CONFIG` is optional.
  When you set it, give an absolute path.

Replace `/srv/kip` with the deployment's absolute path. Stdout carries only the
MCP protocol and logs go to stderr. Pass `KIP_WORKSPACE`, `KIP_PRINCIPAL_ID`
and `KIP_ACL_SCOPES` (and `KIP_ROLES=admin` only for a verified reviewer) as
environment entries, the same way as `KIP_CONFIG` below; root options such as
`kip --workspace acme mcp` reach the server as those variables too.
`KIP_ACL_SCOPES` is comma-separated all the way to the database session, so a
scope never contains a comma; `kip --acl-scope` rejects one that does.

Setup writes the deployment's own `.mcp.json` in the absolute form. A
`.mcp.json` from an earlier setup keeps `bash scripts/mcp.sh` with a relative
`KIP_CONFIG`, because upgrades preserve the file, and it only works when the
client starts in the deployment root. `kip doctor` reports that as the
non-required `mcp_registration` warning, with the absolute entry in
`details.replacement`. Doctor never rewrites the file: edit it by hand, or
apply an approved setup plan again, which rewrites it. The plan lists it in
`replaced_files`, and the apply receipt's `summary` names the kept
`.mcp.json.previous` (the copy that apply replaced) and `.mcp.json.original`
(the earliest copy, never overwritten).

The commands below were checked against Claude Code 2.1.266 and codex-cli
0.144.6 with a throwaway `HOME`, reading back the configuration each one wrote.

### Claude Code

User scope makes the server available in every project for this user (stored
in `~/.claude.json`):

`/abs/path/kip` is the launcher path the installer printed (default
`~/.local/bin/kip`, expanded). A client started before that PATH change does
not see a bare `kip`.

```bash
claude mcp add --scope user kip -- '/abs/path/kip' mcp

# Without the launcher
claude mcp add --scope user kip \
  -e KIP_CONFIG=/srv/kip/config/kip.host.generated.toml \
  -- bash /srv/kip/scripts/mcp.sh
```

Project scope writes the entry to `.mcp.json` in the current directory and
shares it with everyone who opens that project. Run it in the project that
should use KIP:

```bash
claude mcp add --scope project kip -- '/abs/path/kip' mcp
```

`claude mcp add` refuses a name that already exists in the target file
(`MCP server kip already exists in .mcp.json`), which is what happens in a
deployment root where setup already registered `kip`. Add `-e NAME=value` for
each identity variable, and use `claude mcp get kip` to see the stored entry
and whether it connects.

### Codex

`codex mcp add` has no scope option; it writes `~/.codex/config.toml`, so the
server is available to every Codex session for this user:

```bash
codex mcp add kip -- '/abs/path/kip' mcp

# Without the launcher
codex mcp add kip --env KIP_CONFIG=/srv/kip/config/kip.host.generated.toml \
  -- bash /srv/kip/scripts/mcp.sh
```

The second command writes:

```toml
[mcp_servers.kip]
command = "bash"
args = ["/srv/kip/scripts/mcp.sh"]

[mcp_servers.kip.env]
KIP_CONFIG = "/srv/kip/config/kip.host.generated.toml"
```

`codex mcp get kip` shows the stored entry.

### Other stdio clients

This is the `mcpServers` JSON shape of a Claude Code project `.mcp.json`, the
file setup writes. Where another client keeps its server list, and whether it
reads the same keys, is defined by that client's documentation; check it before
copying the entry.

```json
{
  "mcpServers": {
    "kip": { "command": "kip", "args": ["mcp"] }
  }
}
```

Without the launcher:

```json
{
  "mcpServers": {
    "kip": {
      "command": "bash",
      "args": ["/srv/kip/scripts/mcp.sh"],
      "env": { "KIP_CONFIG": "/srv/kip/config/kip.host.generated.toml" }
    }
  }
}
```

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
