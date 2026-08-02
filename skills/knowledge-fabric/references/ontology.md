# Ontology and assertions

The ontology under `ontology/` is a versioned meaning contract. PostgreSQL columns and Neo4j labels are projections, not the ontology itself.

Before adding a predicate:

1. Search existing definitions and aliases.
2. Specify domain, range, direction, inverse, temporal behavior, and review policy.
3. Distinguish deterministic source relations such as `reply_to` from semantic relations such as `responds_to`.
4. Require evidence and human review for legal, financial, approval, amendment, supersession, satisfaction, or violation predicates.
5. Write an ontology migration when changing meaning, not merely spelling.

Graph databases are optional read projections. Approved assertions and their evidence remain canonical in PostgreSQL.
