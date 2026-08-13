# ADR-037: Align the production search contract and readiness gates

- **Status:** Accepted
- **Date:** 2026-08-13

## Context

The target documentation described pgvector as extension-optional and HNSW as
a later manual optimization, while the supported PostgreSQL migration stream
already required pgvector. Search behavior also reached one application service
through parameter-incomplete edge signatures, capability reporting could imply
semantic readiness without an active complete space, and a missing private
corpus could turn the only real-corpus regression gate into a skip.

Contract-safe retrieval was not enough for final answers. Context-free probes
showed that words related to a document could be present while the requested
fact was absent, and short multi-document questions could be ambiguous. These
cases need explicit refusal outcomes rather than another broad ranking
heuristic.

## Decision

1. `SearchRequest` is the only public search-planning contract. CLI, REST, MCP,
   and the Python SDK expose `query`, `limit`, optional
   `lexical|vector|hybrid|reranked` mode, source kinds, document types, project
   IDs, and candidate-assertion inclusion. Identity and ACL remain in the
   verified `RequestContext`.
2. Explicit local CLI ACL options replace ambient `KIP_ACL_SCOPES`; an explicit
   empty set stays empty. With no option, the configured value is used.
3. `capabilities.semantic_search` means a compatible, complete, verified active
   space is ready. Configuration intent and projection state are separately
   reported.
4. The supported PostgreSQL production reference profile requires pgvector and
   migration 0018's 1024-dimensional cosine HNSW index. Queries use bounded
   strict iterative scan settings. Semantic retrieval remains disabled by
   default and still needs measured promotion plus explicit activation.
5. Hosted CI always runs 100 deterministic positive search contracts and 20
   ACL-negative cases. The real private-corpus gate remains separate; protected
   runners set `KIP_REQUIRE_PRIVATE_GOLDEN=1` so missing evidence fails closed.
6. The answer service returns typed `answer_not_present` when retrieved evidence
   does not contain the requested identifier, numeric target, or focused fact,
   and `clarification_required` for bounded short multi-document ambiguity.
   Exact evidence reopen, freshness, and ACL checks still precede success.
7. Original XLSX values, including dates and datetimes, must cross the versioned
   JSON edge without lossy ad-hoc string handling.

## Consequences

- Pgvector installation, HNSW readiness, semantic projection activation, and
  the deployment's default retrieval mode are four distinct states.
- Explicit shadow modes are observable through every edge but cannot make an
  unready space appear active.
- The portable gate blocks deterministic contract and authorization regressions
  without distributing private evidence. It cannot support a corpus-quality or
  industry-ranking claim.
- Conservative answer refusals can increase until reviewed end-to-end cases
  calibrate the adequacy boundary. Refusal reasons remain machine-readable.
- An extension-free PostgreSQL distribution would require a separate supported
  profile and test matrix; it is not implied by the current reference profile.
- Any later search field, activation criterion, or answer-adequacy change must
  update the canonical documents and generated contracts in the same change.

## References

- `docs/PRD.md`
- `docs/TRD.md`
- `docs/DATA_CONTRACTS.md`
- `docs/PRODUCTION_DESIGN_ALIGNMENT.md`
- `evaluation/golden/production-regression.yaml`
- `migrations/0018_embeddings_1024_hnsw.sql`
