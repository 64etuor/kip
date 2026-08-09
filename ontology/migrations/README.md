# Ontology migrations

Breaking ontology releases require a reviewed `kip.ontology-migration.v1` YAML manifest. A migration maps source symbols in the old release to target symbols in the new release using `rename`, `deprecate`, `replace`, `split`, or `merge` operations.

Migration creates target-version assertion candidates. It never rewrites approved assertions in place, and operations affecting existing assertions must set `review_required: true`.

Run `kip ontology diff --migration ...` before
`kip ontology migrate-materialize --migration ...`. Candidate fingerprints bind
the manifest digest, source assertion, target predicate, exact evidence, and
validity interval, so retries deduplicate. Predicate operations are executable;
an entity-type operation that affects a live entity fails closed until an
identity-history migration is supplied.
