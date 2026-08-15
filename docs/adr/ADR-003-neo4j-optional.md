# ADR-003: Neo4j is optional and non-canonical

Status: Accepted — mechanism amended by ADR-046 (2026-08-15)

> Amendment: the pre-provisioned swap scaffolding this ADR described
> (`GraphProjectionPort`, the stub Neo4j adapter, the `graph.backend`
> configuration key, and the `neo4j` packaging extra) was never wired and
> has been removed (ADR-046). The decision itself stands: graph traversal
> is a repository capability today, and Neo4j — if the adoption gate ever
> passes — arrives as a disposable read projection behind a port
> introduced at adoption time.

Use PostgreSQL recursive graph queries first. Add Neo4j only after the adoption gate and parity tests pass. It must be disposable and rebuilt from approved assertions.
