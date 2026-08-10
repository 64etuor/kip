# ADR-032: Use consent-based interaction memory and staged ontology discovery

## Status

Accepted

## Context

A reusable starter cannot assume a research-project ontology before it has seen
the deploying organization's vocabulary. Starting with a completely mutable or
automatically learned ontology would instead make untrusted source text, one
user's phrasing, or a model suggestion into shared meaning. Agent-memory
systems are useful for asking focused follow-up questions and remembering an
explicit preference, but their free-form global memory pattern is not suitable
for canonical knowledge or organizational policy.

## Decision

KIP keeps the small YAML core as the invariant meaning kernel and adds a
selected domain profile. The starter configuration selects `empty`, while the
existing `research-project` profile remains an explicit example. Setup asks for
the profile and separately asks whether interaction persistence is disabled or
requires explicit consent.

When interaction persistence is enabled, clarification questions are
owner-scoped, have at most four choices, and expire after a bounded TTL. An
answer is not durable memory by default. It becomes a `UserPreference` only
when the caller sets `remember=true` for a question with a preference key, or
submits a direct write with `confirmed=true`. Feedback is a separate structured
record of outcome and controlled reason codes. It has no raw query, answer,
source body, or free-form comment field and does not extend `QueryTrace`.

An explicit, confirmed ontology proposal creates an
`OntologyDiscoveryCandidate`, scoped to the selected profile and the submitting
principal. Repeated proposals by that principal increment a candidate count.
Reviewers may mark it `accepted_for_release` or `rejected`; neither status
changes YAML, the active catalog, entities, assertions, retrieval, or answers.
A maintainer still creates a reviewed ontology release and follows the existing
validate/diff/migration workflow.

PostgreSQL stores the interaction state in a dedicated schema and the discovery
candidate state in `knowledge`. Owner RLS protects questions, preferences, and
feedback. Reviewer access requires the verified admin role. Expired questions
have an explicit administrative prune operation. CLI, REST, and MCP call the
same application service and no normal search or answer path reads or writes
this state.

## Consequences

- Migration `0015` adds RLS-protected canonical interaction storage and
  discovery-candidate lifecycle records.
- The setup-generated configuration leaves both interaction memory and adaptive
  discovery disabled unless the operator selects `explicit_consent`.
- `KIP_ROLES=admin` is required for MCP discovery review/list operations;
  ordinary MCP callers can still create their own bounded clarification and
  confirmed proposal records when the feature is enabled.
- Query traces remain a closed redacted operational contract. Interaction data
  is not telemetry and cannot become a training set or an approved fact without
  a separate review process.
