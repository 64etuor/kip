# Data Contracts

All edge adapters exchange canonical Pydantic models and versioned JSON envelopes. Static JSON Schema files are generated into `contracts/`.

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

## Canonical source sequence

```text
SourceObject -> SourceRevision -> Artifact -> ExtractionRun -> ContentUnit
```

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

## Knowledge sequence

```text
Entity -> AssertionCandidate -> ApprovedAssertion -> AssertionEvidence
```

`AssertionExplanation` is a read model that combines one approved assertion with the exact `EvidenceRead` units supporting it. It is not stored as a second source of truth.

`ApprovedAssertion.evidence_acl_snapshot_ids` is the denormalized freshness
guard for its reviewed evidence. It is rebuilt from canonical evidence and does
not replace `AssertionEvidence` or its exact locator.

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
