# Ontology Guide

Ontology files define meaning and validation, not storage implementation.

## Rules

- Reuse a precise predicate before creating a new one.
- Avoid `related_to` except as a temporary curation state.
- Separate deterministic source relationships from semantic business assertions.
- `reply_to` is a source relation; `responds_to` is a business assertion.
- `amends`, `supersedes`, `approves`, `evidences`, and `violates` require evidence and human review.
- Every ontology release is immutable. Changes create a new release and migration note.

## Graph projection

PostgreSQL and optional Neo4j adapters compile from the same approved assertion set. Neo4j labels and relationship types are mappings, not the ontology source of truth.
