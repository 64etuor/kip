# ADR-025: Reviewed ontology mining jobs

- Status: Accepted
- Date: 2026-08-09

## Context

Typed relation proposals existed, but there was no production path from a
configured generator to reviewable ontology state. Running mining during
retrieval would couple latency and side effects to answers, while persisting
model-discovered entities directly would silently turn probabilistic output
into facts. An asynchronous worker also cannot safely widen itself to all
workspace content.

## Decision

Relation mining is an explicit, idempotent `ontology.mine` job. The job key
covers workspace, submitting principal and scopes, access snapshot, active
ontology version, miner model revision, and sorted evidence-unit IDs. The
worker reconstructs that captured context and rejects an expired dynamic access
snapshot before exact evidence reads.

The miner reuses the provider-neutral structured generator and central egress
policy. Its output schema is closed. The adapter validates ontology entity
types, predicates, inherited domain/range, existing entity IDs, evidence IDs,
proposal limits, duplicate semantics, temporal intervals, and pinned model
revision.

Discovered entities and relations persist in separate candidate tables.
Entity approval creates a new ACL-scoped entity in the same PostgreSQL
transaction; relation approval creates a separate assertion. Neither candidate
kind participates in graph traversal or answers before approval. CLI, REST,
MCP, and the Python SDK call the same application service.

## Consequences

- Search and answer paths remain read-only with respect to mining state.
- Prompt injection in evidence cannot change the mining instruction or approve
  a candidate.
- Duplicate worker deliveries do not create duplicate candidate IDs.
- Reviewers cannot list, read, approve, or reject candidates whose complete
  evidence set is outside their current ACL or freshness window.
- Operators must deliberately enable relation mining and provision a worker;
  configuration alone never auto-promotes model output.
