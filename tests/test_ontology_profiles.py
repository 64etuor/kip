from __future__ import annotations

from pathlib import Path

import pytest

from kip.errors import ValidationError
from kip.ontology import OntologyCatalog, validate_ontology
from kip.ontology_release import OntologyRelease

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_loads_empty_domain_without_research_project_symbols() -> None:
    # Given the starter ontology root and its empty domain profile
    ontology_root = ROOT / "ontology"

    # When the catalog is loaded for a new deployment
    catalog = OntologyCatalog.load(ontology_root, domain_profile="empty")

    # Then generic kernel symbols remain available but exemplar symbols do not
    catalog.validate_entity_type("Document")
    with pytest.raises(ValidationError, match="unknown ontology entity type"):
        catalog.validate_entity_type("OfficialLetter")
    assert validate_ontology(ontology_root, domain_profile="empty") == []


def test_catalog_rejects_an_unknown_domain_profile() -> None:
    # Given a nonexistent profile name
    ontology_root = ROOT / "ontology"

    # When/Then profile resolution fails before catalog construction
    with pytest.raises(ValidationError, match="unknown ontology domain profile"):
        OntologyCatalog.load(ontology_root, domain_profile="missing-profile")


def test_release_composition_uses_the_selected_domain_profile() -> None:
    # Given the starter ontology root and its empty domain profile
    ontology_root = ROOT / "ontology"

    # When a release is composed for a new deployment
    release = OntologyRelease.load(ontology_root, domain_profile="empty")

    # Then migration tooling sees only the core kernel and selected profile
    assert "Document" in release.entities
    assert "OfficialLetter" not in release.entities
