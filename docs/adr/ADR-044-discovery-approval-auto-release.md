# ADR-044: Discovery approval materializes an additive ontology release

- **Status:** Accepted
- **Date:** 2026-08-14

## Context

The adaptive discovery loop captured entity-type and predicate proposals and
let an admin mark them `accepted_for_release`, but promoting an accepted
candidate into `ontology/` remained a manual YAML edit plus a version bump.
That broke the conversational curation loop at its last step: vocabulary
discovered and approved in conversation still waited on a human editing
files. The owner decided approval itself must produce the release.

Constraints that shaped the design:

- Ontology files stay the meaning contract (architecture rule 4); the release
  must land in the YAML tree, not in a database-only overlay.
- `core/predicates.yaml` and domain profiles carry comment blocks, so a YAML
  round-trip rewrite would destroy them.
- `validate_ontology` requires `policies/review-policy.yaml` to exactly match
  the review-required predicate set, and ADR-043 rejects core-name collisions.
- The runtime catalog is loaded from core for predicates, so new predicates
  must land in `core/predicates.yaml`; new entity types belong to the active
  domain profile.
- Graph queries do not filter by ontology version unless asked, and candidate
  validation resolves the active catalog version dynamically, so an additive
  minor version bump does not hide existing assertions.
- Container images ship `ontology/` read-only (`chmod -R a-w`).

## Decision

1. `OntologyDiscoveryProposal` carries optional spec fields: `parent` for
   entity types; `domain`, `range`, `inverse`, `risk`, `review`,
   `extraction` for predicates. CLI, REST, and MCP propose surfaces expose
   them.
2. Approving an `entity_type` or `predicate` candidate materializes the
   release before the status transition is persisted:
   - entity types are appended to the active domain profile with the
     proposal's label/definition as presentation metadata; `parent` falls
     back to a valid `target_symbol`, else the type becomes a root;
   - predicates are appended to `core/predicates.yaml`; absent spec fields
     default fail-safe to `domain`/`range` `["EvidenceObject"]`,
     `inverse: null`, `risk: high`, `review: required`,
     `extraction: semantic`; a review-required predicate is synced into
     `policies/review-policy.yaml` in the same materialization;
   - the touched file's `version` is bumped minor; edits are targeted text
     insertions (comment-preserving), shadow-validated on a temp copy of the
     whole tree with `validate_ontology`, then applied atomically;
     materialization is idempotent on retry and fails closed on a read-only
     ontology root.
3. The review response carries a `kip.ontology-release.v1` `release` object
   (`kind`, `symbol`, `file`, `version`, `catalog_refresh`).
   `catalog_refresh` is `"restart_required"`: long-running API/worker/MCP
   processes hold an immutable catalog snapshot and load the release on
   restart; every fresh CLI invocation sees it immediately.
4. `controlled_value` and `alias` discovery candidates keep the previous
   behavior (status only; no materialization).
5. `FALLBACK_EVIDENCE_REQUIRED_PREDICATES` becomes an explicit fail-closed
   floor (subset of the derived set) instead of an exact pin, so additive
   releases on a deployment do not break the governance contract test.
6. Hardcoded `"core/1.0.0"` defaults on candidate-proposal surfaces are
   replaced by resolution of the active catalog version, so releases do not
   strand the propose paths on a stale version string.

## Consequences

- The conversational loop closes: propose in conversation, admin approves,
  the ontology release exists — no manual YAML step. Assertions using
  auto-released predicates still require exact evidence and human review, so
  no fact is created automatically.
- Deployments review released vocabulary through version control history of
  `ontology/`, and `kip ontology diff` classifies the additions as
  compatible.
- Both Compose profiles bind-mount the version-controlled ontology checkout
  (`KIP_ONTOLOGY_PATH`) read-write into the API and read-only into the
  worker, and the image no longer bakes `ontology/` immutable, so
  auto-release works in containers by default and releases persist across
  restarts under git review. A missing or read-only mount still fails closed
  with an actionable error.
- Long-running processes serve the previous catalog until restarted; the
  release payload states this explicitly.
- The Postgres store persists the full proposal spec (migration 0021) so a
  candidate reviewed after a restart materializes with its original spec.

## Evidence

- `tests/test_ontology_discovery_release.py` (materializer: roots, explicit
  parents, comment preservation, review-policy sync, idempotency, read-only
  and invalid-spec fail-closed paths).
- `tests/test_adaptive_interactions.py` (approve materializes before status;
  reject never materializes; failed materialization leaves the candidate
  proposed; running-process catalog snapshot vs fresh load).
- `tests/test_ontology_review_governance.py` (fallback floor semantics).
- `tests/test_cli_surface.py`, `tests/test_api.py` (active-catalog version
  defaults).

## References

- `src/kip/ontology_discovery_release.py`
- `src/kip/application/interactions.py`
- `src/kip/domain/interactions.py`
- ADR-043 (collision-safe validation this release path relies on)
- `docs/ONTOLOGY_GUIDE.md`, `docs/TRD.md` section 24, `docs/SECURITY.md`
