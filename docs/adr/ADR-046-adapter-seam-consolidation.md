# ADR-046: Adapter seam consolidation for tool swappability

- **Status:** Accepted
- **Date:** 2026-08-15

## Context

A three-agent hexagonal-architecture audit confirmed the ports/adapters
discipline holds (vendor-neutral ports, zero layering violations, single
composition root) but found seams where the promise "swap a tool by adding
an adapter plus configuration" was not actually true:

- The graph capability's swap surface was decorative: `GraphProjectionPort`
  was referenced by nothing, `Neo4jProjectionAdapter` was never constructed,
  and the `graph.backend` configuration key selected nothing — the real
  graph contract lives in `KnowledgeStore.graph_neighbors`/`graph_path`.
- The embedding dimension 1024 was hardwired into the Postgres table name
  and three code guards, so a different-dimension embedding model could not
  be adopted through configuration.
- Event-connector fan-out was hardcoded by source name in three layers
  (application sync methods, event-family table, CLI/worker dispatch), so a
  new connector required application edits.
- Smaller leaks: representation-role extension table and the XLSX extension
  whitelist lived in application code, the reranker best-first ordering was
  an unwritten convention replicated in four adapters, generation-provider
  dispatch was a closed string `if/elif`, and the graph-path result cap was
  a magic number duplicated across two adapters.
- The only multi-implementation behavioral test was edge parity; memory and
  Postgres repositories shared no behavior-parity suite.

## Decision

1. **Graph is a repository capability.** The orphaned port, the dead Neo4j
   adapter, the `[graph]` configuration section, and the unused `neo4j`
   packaging extra are removed. `capabilities.graph_backend` (field kept,
   schema unchanged) now reports the active repository backend's own name.
   A Neo4j read projection, if adopted later, introduces its own port at
   adoption time behind the existing adoption gate.
2. **Embedding dimensions are provisioned, not hardwired.** The Postgres
   adapter resolves the embedding table from a closed
   dimensions-to-table registry (1024 and 1536, the latter provisioned by
   append-only migration 0022 with its own HNSW index); unsupported
   dimensions fail with the provisioned list. Status/projection queries
   union all provisioned tables, which also fixes multi-space counting.
3. **Connectors declare their event family.** `SourceCatalogPort` gains
   `event_family(source_name)`; application sync is one generic
   `sync_remote`, event formatting branches on the declared family, and the
   registry derives `capabilities()`/`enabled_names()` from the source enum.
   Onboarding a new connector now touches only the adapter module, the
   enum, its match arms, and configuration — proven by a test that onboards
   a fake connector without application edits.
4. **Seam polish.** Representation roles are declared by the parser
   registry (`ParserRegistryPort.representation_role`); XLSX range-read
   capability is asked of `WorkbookReaderPort.supports`; the reranker
   best-first ordering is documented on the port and enforced defensively
   in `apply_rerank`; generation-provider dispatch uses a `StrEnum` with an
   exhaustive `match`/`assert_never` like the reranker; the graph-path
   result cap is the shared domain constant `GRAPH_PATH_RESULT_CAP`.
5. **Behavior parity is tested.** A contract suite runs six identical
   scenarios against both repositories (ingest/search/read, ACL denial,
   graph direction and approved-only, path cap, candidate lifecycle, job
   lifecycle); the layering rule check now also covers the domain layer and
   direct vendor-SDK imports.

## Consequences

- Reranker, generation, connectors, and embedding dimensions are now
  swappable through adapter + composition root + configuration, with the
  reranker-style exhaustive-enum dispatch as the house idiom.
- Documented divergence (accepted, not hidden): the memory repository does
  not enforce the Postgres path's fail-closed ACL-snapshot requirement on
  `ingest_packet` (memory is a test/demo backend); the parity suite pins
  the mutually enforced contract. A latent memory-adapter bug (graph-path
  cap not enforced within a single BFS fan-out) was found and fixed by the
  parity work.
- The text analyzer remains intentionally unpluggable (constructed
  directly in the composition root): swapping tokenization requires a
  lexical projection rebuild, so it stays a deliberate code-level change.

## Evidence

- `tests/contract/test_repository_behavior_parity.py` (6 scenarios × 2
  backends), `tests/test_event_connector_genericity.py`,
  `tests/test_postgres_embeddings_table_spec.py`,
  `tests/integration/test_postgres_semantic_projection.py` (1536
  round-trip, unsupported-dimension refusal),
  `tests/characterization/test_application_dependency_rules.py` (domain +
  vendor-SDK coverage).

## References

- `src/kip/adapters/repository/postgres/database.py` (`_EMBEDDING_TABLES`)
- `migrations/0022_embeddings_1536_projection.sql`
- `src/kip/application/ingestion.py` (`sync_remote`),
  `src/kip/adapters/connectors/registry.py` (`event_family`)
- `src/kip/adapters/generators/provider.py`, `src/kip/domain/models.py`
  (`GRAPH_PATH_RESULT_CAP`)
- `docs/TRD.md` §1.4, `docs/PRD.md` (Neo4j adoption-gate wording)
