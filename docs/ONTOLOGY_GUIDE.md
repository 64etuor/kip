# Ontology Guide

Ontology files define meaning and validation, not storage implementation.

## Files and enforcement scope

The loaders (`OntologyCatalog.load`, `OntologyRelease.load`) enforce
`core/entity-types.yaml`, `core/predicates.yaml`, the active
`domains/<profile>.yaml`, and `policies/review-policy.yaml`. The catalog is
loaded eagerly at container build, so a broken contract fails startup, not
query time. `validate_ontology` additionally rejects:

- a domain profile that redefines a core entity type or core predicate name
  (`<file>: entity type <name> redefines core entity type`);
- a `source_object_types` entry in `ontology/sources/*.yaml` whose `parent`
  is not a known entity type in core plus the active domain profile.

The following are advisory: `mappings/property-graph.yaml`,
`policies/acl-policy.yaml`, domain `controlled_values`, and the
`source_object_types`/`locators` content of `ontology/sources/*.yaml` beyond
the parent check above (only their `deterministic_relations` key is consumed
at runtime). Advisory files get YAML-syntax and `version`-field validation
only; do not rely on them to enforce behavior.

## Rules

- Reuse a precise predicate before creating a new one.
- Avoid `related_to` except as a temporary curation state.
- Separate deterministic source relationships from semantic business assertions.
- `reply_to` is a source relation; `responds_to` is a business assertion.
- Every predicate the catalog marks `review: required` or `risk: high`
  (currently `amends`, `supersedes`, `approves`, `evidences`, `responds_to`,
  `records_decision`) requires exact evidence and human review. The
  enforcement set is derived from `ontology/core/predicates.yaml` at approval
  time — never from a hardcoded list — and a contract test pins the
  store-level fallback set to the same file.
- Low-risk `review: not_required` predicates gain a measured auto-approve
  lane (ADR-047): a mined candidate is auto-approved only when the miner's
  per-predicate precision over at least `min_reviewed` HUMAN decisions
  clears `min_precision` and the candidate clears `min_confidence`
  (`[ontology.auto_approve]`, fail-closed on every axis; auto-approved
  decisions never count toward their own statistic). Auto-approvals run
  through the same approval path as human review, carry the
  `auto-approve-policy/v1` marker with the measured precision, are reported
  in the mining result payload, and stay revocable.
- Every ontology release is immutable. Semantic changes (types, predicates,
  domain/range, risk, review, extraction, inverses) create a new release and
  migration note.

## Discovery auto-release (ADR-044)

Approving an `entity_type` or `predicate` discovery candidate materializes an
additive release automatically: entity types land in the active domain
profile, predicates land in `core/predicates.yaml` (review-required
predicates are synced into `policies/review-policy.yaml` in the same step),
the touched file's version bumps minor, and the edit is comment-preserving,
shadow-validated, idempotent, and atomic. Absent predicate spec fields
default fail-safe (`domain`/`range` `["EvidenceObject"]`, `risk: high`,
`review: required`, `extraction: semantic`), so assertions using an
auto-released predicate still require exact evidence and human review.
Materialization is serialized per ontology root by a file lock
(`.release.lock`), and a two-file predicate release is journaled
(`.pending-release.json`) so a crash between the two writes heals
automatically on the next materialization or process start instead of
leaving an unloadable tree. Approving a different candidate that reuses an
already-released symbol with different content is refused as a conflict.
Long-running API/worker/MCP processes pick the release up on restart
(`catalog_refresh: "restart_required"` in the review response); every fresh
CLI invocation sees it immediately. Compose deployments bind-mount the
version-controlled checkout (`KIP_ONTOLOGY_PATH`) read-write into the API
and read-only into the worker, so releases persist and stay reviewable
through git; a missing or read-only mount fails closed. `controlled_value`
and `alias` candidates are status-only and still need a manual release.

## Labels and descriptions

Entity types and predicates carry optional presentation metadata:
`description`, `label_ko`, and `description_ko`. These keys are surfaced in
the mining contract (`entity_type_labels`, per-predicate `label_ko` /
`description`) and in candidate review listings
(`predicate_label_ko`, `predicate_description`). They are display metadata
only: adding or editing them never changes meaning, so it is a non-breaking
metadata change that does not bump the release version and does not require
a migration manifest.

## Mining workflow (two passes)

Relation proposals can only reference entities that already exist and are
approved. A first mining run over new evidence therefore usually produces
entity candidates plus relation skips that name the missing entities. The
working loop is:

1. `kip ontology mine --unit-id ...` — first pass. New entities become
   `EntityCandidate` records; relations that reference them are skipped with
   the reason `unknown existing entity: ... (approve the referenced entity
   candidate, then re-run mining)`.
2. Review and approve the entity candidates
   (`kip ontology candidates`, `kip ontology entity-approve`).
3. `kip ontology mine --unit-id ...` again with the same units — second
   pass. Approving entities changes the approved-entity digest bound into
   the mining job idempotency key, so the re-run is a new job, not a dedup
   onto the finished one. Relations now resolve and become
   `AssertionCandidate` records.
4. Review and approve the relation candidates (`kip review list`,
   `kip review approve`).

A single invalid, duplicate, or stale-evidence proposal never fails the
batch: it is skipped and reported per proposal in the mining summary and in
the durable job's `payload.result.skipped` (visible via `kip jobs list`, the
REST `/v1/jobs` endpoint, and the MCP `kip_jobs` tool). Stale evidence units
stay fail-closed per unit: they are excluded from mining and reported, never
silently mined.

## Revocation and supersession

Approval is not irreversible:

- `kip review revoke ASSERTION_ID --note "..."` (REST
  `POST /v1/review/assertions/{id}/revoke`, MCP
  `kip_ontology_assertion_revoke`) transitions an active assertion to
  `revoked`. A non-empty note is required and the acting principal plus
  timestamp are recorded. Revoked assertions stay stored for audit but are
  excluded from every approved-only surface: graph neighbors/paths, ontology
  answer context, contradiction checks, and assertion listings.
- When a candidate records `contradicts_assertion_ids`, the reviewer may
  approve it with `--supersede-contradicted` (REST/MCP
  `supersede_contradicted=true`). The contradicted active assertions are
  marked `superseded` with `superseded_by` pointing at the newly approved
  assertion, in the same transaction. Without the flag, contradictions are
  resolved manually via `kip review revoke`.

## Graph projection

Graph traversal compiles from the approved assertion set inside the active repository backend. If a Neo4j read projection is adopted later, its labels and relationship types are mappings, not the ontology source of truth.
