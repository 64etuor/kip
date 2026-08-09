# ADR-027: Materialize ontology changes as reviewed assertion candidates

## Status

Accepted

## Context

Ontology diff and manifest coverage identified breaking changes but did not
move an existing reviewed corpus to a target release. Updating approved rows in
place would destroy audit history, bypass evidence review, and make rollback
ambiguous. A retry also needs to be safe after a worker or database failure.

## Decision

KIP scans active source-version assertions through the canonical knowledge port,
with ACL and dynamic snapshot freshness applied in the repository query. A
reviewed predicate rename, replace, split, or merge creates target-version
`AssertionCandidate` records. Deprecation records affected counts without
creating a replacement. Each candidate preserves source assertion IDs, exact
revision-bound evidence, validity, target risk, manifest SHA-256, and migration
derivation.

Candidate IDs are deterministic. A repeated run returns the existing IDs.
Materialization validates and reopens all candidate inputs before persistence.
It never changes the source assertion status; approval and supersession remain
separate human actions.

The review service validates against the active ontology catalog. It therefore
rejects a target-version candidate until that release is deployed as the active
meaning contract. Application graph traversal passes the same active version to
the repository, so old and future approved assertions cannot mix in one normal
answer graph.

KIP rejects a manifest that affects existing assertions without
`review_required: true`. It also rejects live entity-type materialization until
the product has a versioned identity-history contract capable of representing
rename, merge, and split without inventing new identity semantics.

## Consequences

- PostgreSQL migration `0011` adds explicit source-assertion lineage to review
  candidates and indexes it for audit queries.
- CLI operators get a versioned materialization receipt and idempotent retries.
- Existing approved facts remain available for rollback and historical audit.
- Entity-type release changes require a deliberate identity migration design;
  they cannot be silently approximated by relation rewrites.
