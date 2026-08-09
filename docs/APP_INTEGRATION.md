# Application Integration

## Preferred integration order

1. REST/OpenAPI for web and backend applications.
2. Python SDK wrapper for scripts and services.
3. MCP for AI clients that support tools.
4. CLI subprocess for local automation and agents.

All four surfaces call the same application services.

## Read API

- `GET /v1/capabilities`
- `POST /v1/search`
- `POST /v1/context`
- `GET /v1/units/{unit_id}`
- `GET /v1/documents/{document_id}`
- `GET /v1/assertions/{assertion_id}`
- `GET /v1/assertions/{assertion_id}/explain`
- `POST /v1/graph/neighbors`
- `POST /v1/graph/path`

## Write/admin API

- `POST /v1/connectors/events`
- `POST /v1/sync/{source_name}`
- `POST /v1/sync/filesystem/{source_name}`
- `GET /v1/jobs`
- `GET /v1/review/candidates`
- `GET /v1/review/candidates/{candidate_id}`
- `POST /v1/review/candidates/{candidate_id}/approve`
- `POST /v1/review/candidates/{candidate_id}/reject`

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

REST, MCP, the CLI, and connector events are edge adapters. They must not implement ranking, ACL policy, ontology promotion, or parser decisions. Add a new client protocol by calling `KnowledgeService` and preserving the checked-in contracts under `contracts/`.
