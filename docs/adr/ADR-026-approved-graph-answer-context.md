# ADR-026: Approved graph answer context

- Status: Accepted
- Date: 2026-08-09

## Context

Lexical and vector retrieval can miss evidence when a user names an entity by
an alias or asks about a reviewed multi-hop relationship. Querying all graph
records would be unsafe because candidates, expired facts, inaccessible paths,
and source-changed evidence must not influence an answer. Passing an approved
edge to a generator without its exact evidence would also turn the graph into
an uncited source of truth.

## Decision

Normal answers resolve normalized canonical names and aliases through the
canonical repository. They traverse bounded PostgreSQL or memory graph paths
using active assertions only and apply current valid-time predicates before
traversal. The application rechecks the interval and reopens every assertion
evidence unit through the same ACL and source-hash boundary as lexical results.

The resulting `kip.ontology-context.v1` read model contains matched entities,
approved edges, paths, and evidence-unit IDs. Exact graph evidence may rescue a
lexical relevance miss caused by an alias. It still passes XLSX, approval,
classification, egress, and citation validation. Generated-answer requests may
include `GenerationRelation` metadata only when all supporting evidence bodies
fit in the request; claims must cite those evidence IDs. Public answers cite
every evidence unit exposed by their ontology context.

Candidates, inactive assertions, future or expired assertions, ACL-invisible
entities or paths, freshness-stale snapshots, and source-changed evidence are
excluded. Search and answer remain read-only and never trigger mining or graph
rebuilds.

## Consequences

- Alias and reviewed multi-hop questions can use exact approved evidence even
  when ordinary lexical relevance misses it.
- PostgreSQL and memory graph traversal share current-time semantics.
- Historical graph questions require a future explicit as-of contract; normal
  answers do not silently include historical facts.
- CLI, REST, MCP, and SDK expose the same ontology context read model.
