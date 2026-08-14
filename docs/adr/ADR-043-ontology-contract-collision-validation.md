# ADR-043: Ontology contract validation rejects core shadowing and unknown source parents

- **Status:** Accepted
- **Date:** 2026-08-14

## Context

`validate_ontology` merged core and domain-profile definitions with
`dict.update`, and `OntologyRelease.load` used `core.entity_types |
domain.entity_types`. Neither path detected a key collision, so a domain
profile that reused a core name (for example `Person`) silently replaced the
core definition repo-wide with no validation error and no failing test. This
was reproduced live during an audit.

Separately, the `source_object_types.*.parent` fields in
`ontology/sources/*.yaml` (for example `EmailMessage.parent: Communication`)
were never cross-checked against the entity-type catalog. A typo'd or removed
parent type passed validation silently, which made those fields dead
documentation rather than part of the meaning contract.

Architecture rule 4 makes `ontology/` the meaning contract. A contract whose
core vocabulary can be silently redefined by a profile, or whose source
mappings can silently reference nonexistent types, is not a reliable
contract.

## Decision

1. `validate_ontology` reports an error when a domain-profile YAML defines an
   entity type or predicate name that already exists in core. Domain profiles
   may only add new names; changing core semantics requires editing core and
   cutting a release.
2. `validate_ontology` reports an error when a `source_object_types` entry in
   `ontology/sources/*.yaml` declares a `parent` that is not a known entity
   type in core plus the active domain profile.
3. `OntologyRelease.load` inherits both checks because it validates through
   `validate_ontology` before merging; a colliding profile now raises
   `ValidationError` instead of silently shadowing core.
4. The remaining unenforced ontology files (`mappings/property-graph.yaml`,
   `policies/acl-policy.yaml`, domain `controlled_values`, and
   `source_object_types` content beyond the parent check) are explicitly
   documented as advisory in `docs/ONTOLOGY_GUIDE.md` and `docs/TRD.md`
   section 24.1 rather than being silently unenforced.

## Consequences

- A domain profile can no longer change the meaning of a core type or
  predicate without failing startup (the catalog is loaded eagerly at
  container build) and failing `kip ontology validate`.
- `ontology/sources/*.yaml` parent references can no longer drift from the
  entity-type catalog undetected.
- Existing deployments are unaffected: the checked-in tree was verified to
  contain no collisions and no unknown parents before the checks landed, and
  the repository contract test (`validate_ontology(ROOT / "ontology") == []`)
  still passes.
- Profile authors who intend to specialize a core concept must introduce a
  new name with `parent` set to the core type instead of redefining the core
  name.

## Evidence

- `tests/test_ontology_contract.py`:
  `test_ontology_contract_detects_domain_entity_type_shadowing_core`,
  `test_ontology_contract_detects_domain_predicate_shadowing_core`,
  `test_ontology_contract_detects_unknown_source_object_parent`,
  `test_ontology_contract_accepts_valid_domain_and_source_definitions`.
- `tests/test_ontology_profiles.py`:
  `test_release_load_rejects_a_domain_profile_that_shadows_a_core_entity_type`.

## References

- `src/kip/ontology.py` (`validate_ontology`)
- `src/kip/ontology_release.py` (`OntologyRelease.load`)
- `docs/ONTOLOGY_GUIDE.md` ("Files and enforcement scope")
- `docs/TRD.md` section 24.1
