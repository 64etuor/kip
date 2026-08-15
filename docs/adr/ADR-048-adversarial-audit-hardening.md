# ADR-048: Adversarial-audit hardening of trust, resilience, and edge contracts

- **Status:** Accepted
- **Date:** 2026-08-15

## Context

A nine-lane adversarial audit (parser fuzzing, auto-approve trust boundary,
ACL across all surfaces, ontology-release concurrency, retrieval/answer
integrity, end-to-end stress, contract/envelope parity, and setup/config)
reproduced ~30 defects against the then-current tree. They cluster into
trust-boundary gaming, missing authorization at the shared layer, edge
contract drift, parser crash/silent-loss failure modes, and ingestion
fragility. This ADR records the decisions that closed them; the trust
invariants themselves (untrusted evidence, ACL-before-retrieval,
candidates-are-not-facts, exact citations) were confirmed intact and are
unchanged.

## Decision

1. **Auto-approve precision is tamper-resistant and revocation-aware, and
   the policy is opt-in** (amends ADR-047). The auto-approve exclusion keys
   off a dedicated `knowledge.assertion_candidates.auto_approved` column
   (migration 0023) set only by the policy path, not a free-text
   `review_note` prefix an ordinary reviewer could spoof. Precision counts
   only human decisions and treats a human approval whose assertion was
   later revoked as counting against precision. `[ontology.auto_approve]`
   defaults **off**; guided setup and the shipped configs never silently
   enable candidate promotion.
2. **Ontology mutation requires admin at the shared application layer**
   (architecture rule 6). `review_approve`/`review_reject`/
   `revoke_assertion`, ontology mining, entity creation, and entity-candidate
   review now enforce the admin role inside the application services, so
   CLI, REST, and MCP all fail closed for non-admins instead of REST-only
   edge enforcement. `graph_neighbors`/`graph_path` require admin to set
   `approved_only=false` (proposed/revoked assertions are not exposed to
   ordinary callers). The auto-approve policy context carries the admin
   role so the measured path is not blocked.
3. **All three edges present the same `kip.envelope.v1` contract.** REST
   wraps the 413 size-guard and any unhandled exception in an envelope
   (no bare traceback); MCP wraps every tool result and `KipError` in the
   envelope (typed `code`, not an opaque ToolError); the CLI maps a raw
   pydantic `ValidationError` to `validation_error`/exit 3 with a clean
   message. The FastAPI/OpenAPI version derives from the package version.
4. **Ontology loading and release fail closed, never crash.**
   `validate_ontology` guards a non-dict predicate value (a null-valued
   predicate no longer crashes container startup with `AttributeError`),
   `complete_pending_release` re-validates a journal on a shadow tree and
   quarantines a corrupt/invalid one instead of writing it or permanently
   crashing startup, and `OntologyDiscoveryCandidate` carries the same
   field validators as the proposal.
5. **Parsers always fail as typed `ParserError` and never silently lose
   content at high confidence.** Broadened exception capture (pymupdf
   `FzErrorBase`, `ElementTree.ParseError`), CSV `\r`-only normalization,
   UTF-16/NUL detection and NUL-safe bodies, DOCX nested-table/textbox
   recovery and `mc:AlternateContent` handling, content-derived quality
   that no longer over- or under-states fidelity, a file-size backstop,
   and a CSV rule-9 refusal (`csv_full_table_required`) for aggregate
   questions answered from a partial CSV chunk. A shared zip-bomb guard
   and recursion-depth limit protect every OOXML parser.
6. **Ingestion isolates and never false-tombstones.** A defensive per-file
   catch keeps one unparseable file from aborting a whole sync; files that
   are present on disk but skipped (oversize or filtered) are no longer
   reconciled as deletions; worker retries use bounded backoff.
7. **Config surface is honest.** Dead keys removed, orphaned Neo4j env/
   compose artifacts deleted, and the guided-setup writer emits the
   promoted bm25 reranker and the opt-in auto-approve section.

## Consequences

- The auto-approve trust boundary can no longer be gamed by a reviewer and
  self-corrects on revocation; nothing auto-promotes without an operator
  opting in.
- MCP and CLI now require an admin role for ontology mutation, matching
  REST — deployments granting those surfaces to non-admin principals must
  set the admin role explicitly for legitimate reviewers.
- MCP tool output is now the envelope shape (a client-visible change);
  documented in the CHANGELOG.
- A single malformed ontology file or one bad source file degrades cleanly
  instead of taking down startup or a whole sync.

## Evidence

- `tests/contract/test_repository_behavior_parity.py` (spoof-resistant and
  revocation-aware precision, workspace scoping),
  `tests/test_ontology_review_governance.py` and
  `tests/test_ontology_rag_cli.py` (admin gates),
  `tests/test_api.py`/`test_cli_envelope_contract.py`/
  `test_mcp_envelope_contract.py` (envelope parity),
  `tests/test_ontology_contract.py`/`test_ontology_discovery_release.py`
  (validation/quarantine), the parser test suites, and
  `tests/test_ingestion_resilience.py`/
  `test_filesystem_deletion_reconciliation.py`.

## References

- migration `0023_assertion_candidate_decision_source.sql`
- ADR-047 (auto-approve policy this amends), ADR-046, architecture rules 6,
  10, 11, 12 (AGENTS.md)
