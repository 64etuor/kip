"""The YAML ontology tree behind :mod:`kip.ports.ontology`."""

from __future__ import annotations

from pathlib import Path

from kip.domain.ontology import OntologyCatalog
from kip.domain.ontology_migration import OntologyDiff
from kip.ontology import load_catalog
from kip.ontology_migration import diff_ontologies


class FilesystemOntologyCatalog:
    """:class:`kip.ports.ontology.OntologyCatalogPort` over `ontology/`."""

    def load(self, root: Path, *, domain_profile: str) -> OntologyCatalog:
        return load_catalog(root, domain_profile=domain_profile)

    def diff(
        self,
        before_root: Path,
        after_root: Path,
        *,
        domain_profile: str,
    ) -> OntologyDiff:
        return diff_ontologies(
            before_root,
            after_root,
            before_domain_profile=domain_profile,
            after_domain_profile=domain_profile,
        )
