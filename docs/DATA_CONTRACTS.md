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

`ContentUnit.lexical_text` is the versioned analyzer output used to rebuild the
lexical projection. It is stored with the extraction because normalized body
text alone cannot reproduce Korean n-grams, title tokens, and stable source
identifiers. The `search.lexical_units` row remains a disposable projection.

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
egress denial, provider unavailability, and invalid generated citations.
Extractive fallback is visible through `retrieval_mode` and `warnings`; it is
never selected unless configuration explicitly permits it.

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
`RelationMiningResult` contains only typed entity and relation proposals plus a
pinned model revision and token usage. The adapter rejects unknown types,
predicates, entity IDs, evidence IDs, domain/range violations, duplicates, and
malformed intervals before persistence. Source text remains a data field and
cannot alter the system instruction.

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

`AssertionExplanation` is a read model that combines one approved assertion with the exact `EvidenceRead` units supporting it. It is not stored as a second source of truth.

`OntologyAnswerContext` is a versioned read model containing ACL-visible matched
entities, current approved edges, bounded paths, and their exact evidence-unit
IDs. The application removes candidates, non-active assertions, future or
expired validity intervals, inaccessible evidence, and source-changed evidence
before constructing it. An answer exposes the context only with citations for
all included graph evidence.

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
at most four stable choice IDs, a caller-only scope, and an expiry. Its answer
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
