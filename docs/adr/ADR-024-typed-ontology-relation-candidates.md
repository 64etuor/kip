# ADR-024: Typed ontology relation candidates

- Status: Accepted
- Date: 2026-08-09

## Context

The original candidate record accepted unstructured evidence JSON and free-form
subject/object IDs. It could validate predicate names but not entity types,
source revision identity, temporal conflicts, or duplicate mining runs.

## Decision

Ontology YAML remains the meaning contract. The runtime catalog loads entity
inheritance and predicate domain, range, risk, extraction, and review policy.
Entities persist normalized canonical names and aliases under ACL scopes.

Relation proposals pass through one ontology application service. It resolves
visible typed entities, validates inherited domain/range compatibility, reopens
fresh evidence, records revision hashes and locators, derives review risk, and
marks overlapping different-object assertions as contradictions.

The candidate fingerprint includes meaning, evidence, validity, and derivation
revision. Its deterministic public ID and a PostgreSQL uniqueness constraint
make repeated mining idempotent. Candidate evidence is normalized into its own
table while the prior JSON field remains a compatibility projection. Migration
`0009` backfills that table and never rewrites approved assertions.

## Consequences

- Parser, rule, model, and human proposals stay review candidates.
- A miner revision or source revision change creates a separately reviewable
  candidate instead of overwriting provenance.
- Approved assertion visibility remains the intersection of all evidence ACLs
  and freshness snapshots.
- Legacy candidate creation remains readable, but production mining must use the
  typed relation-proposal service.
