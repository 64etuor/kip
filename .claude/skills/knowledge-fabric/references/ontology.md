# Ontology and assertions

The ontology under `ontology/` is a versioned meaning contract. PostgreSQL columns are projections, not the ontology itself — and if a graph database is ever adopted, its labels would be a projection too.

Before adding a predicate:

1. Search existing definitions and aliases.
2. Specify domain, range, direction, inverse, temporal behavior, and review policy.
3. Distinguish deterministic source relations such as `reply_to` from semantic relations such as `responds_to`.
4. Require evidence and human review for legal, financial, approval, amendment, supersession, satisfaction, or violation predicates.
5. Write an ontology migration when changing meaning, not merely spelling.

Evidence enforcement is derived from the loaded catalog: any predicate with `review: required` or `risk: high` in `ontology/core/predicates.yaml` cannot be approved without exact evidence. Korean labels (`label_ko`, `description_ko`) are presentation metadata and never change meaning.

Approval is reversible: `review revoke` transitions an active assertion to `revoked` with a required note, removing it from all approved-only surfaces while keeping the audit record. Candidates that contradict an active assertion record the conflict; approving with `--supersede-contradicted` retires the contradicted assertion as `superseded`.

Graph databases are optional read projections. Approved assertions and their evidence remain canonical in PostgreSQL.

## Mining depends on reviewed entities

Mined relations can reference only already-approved entities. Mine the selected
units, inspect entity candidates, obtain the human review decision, then re-run
the same mining command after entity approval. Approval changes the job digest,
so re-mining produces relation candidates instead of deduplicating the job.
Inspect `jobs list` results (`payload.result.skipped` and `last_error`) for
per-proposal omissions; successful jobs can still skip proposals.

`review propose` creates a human-origin candidate and never auto-approves it.
Use the current CLI help or MCP schema for review arguments rather than
assuming a candidate type is interchangeable with another queue.

## Discovery changes the schema

New entity types and predicates belong in `ontology discovery`, not the entity
instance queue. Discovery review requires admin authorization. Accepting a
candidate materializes an additive, shadow-validated YAML release (ADR-044).
New predicates default to high-risk/review-required. If the response reports
`catalog_refresh: restart_required`, long-running services must restart to
load it; a new CLI invocation reads it immediately. Do not silently restart
services as a side effect of retrieving knowledge.
