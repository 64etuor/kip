"""The two ontology adapters must satisfy the ports the use cases hold.

The use cases now reach `ontology/` only through
`kip.ports.ontology.OntologyCatalogPort` and
`kip.ports.ontology.OntologyReleaseWriterPort`. A signature that drifts from
either Protocol would still import and still type-check inside the adapter, so
the conformance is pinned here against the shipped tree.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kip.adapters.ontology import (
    FilesystemOntologyCatalog,
    FilesystemOntologyReleaseWriter,
)
from kip.adapters.ontology.release import RELEASE_JOURNAL_FILENAME
from kip.domain.interactions import OntologyDiscoveryCandidate
from kip.errors import ValidationError
from kip.ports.ontology import OntologyCatalogPort, OntologyReleaseWriterPort

ROOT = Path(__file__).resolve().parents[1]


def _ontology_root(tmp_path: Path) -> Path:
    target = tmp_path / "ontology"
    shutil.copytree(ROOT / "ontology", target)
    return target


def test_the_catalog_adapter_satisfies_the_catalog_port() -> None:
    # Given the shipped ontology tree
    catalog: OntologyCatalogPort = FilesystemOntologyCatalog()

    # When it is read through the port
    loaded = catalog.load(ROOT / "ontology", domain_profile="research-project")

    # Then the domain catalog it returns carries the released vocabulary
    assert loaded.domain_profile == "research-project"
    assert loaded.version.startswith("core/")
    assert loaded.predicates
    assert loaded.entity_parents


def test_the_catalog_adapter_diffs_two_trees_through_the_port(tmp_path: Path) -> None:
    # Given two copies of the same released tree
    catalog: OntologyCatalogPort = FilesystemOntologyCatalog()
    before = _ontology_root(tmp_path / "before")
    after = _ontology_root(tmp_path / "after")

    # When they are diffed through the port
    diff = catalog.diff(before, after, domain_profile="research-project")

    # Then an unchanged release classifies as compatible with no changes
    assert diff.changes == ()
    assert diff.classification == "compatible"
    assert diff.from_version == diff.to_version


def test_the_catalog_adapter_refuses_an_invalid_tree(tmp_path: Path) -> None:
    # Given a tree whose predicate file no longer parses
    catalog: OntologyCatalogPort = FilesystemOntologyCatalog()
    root = _ontology_root(tmp_path)
    (root / "core" / "predicates.yaml").write_text("version: not-semver\n", encoding="utf-8")

    # When it is loaded through the port
    # Then the adapter fails closed instead of returning a partial catalog
    with pytest.raises(ValidationError, match="invalid ontology contract"):
        catalog.load(root, domain_profile="research-project")


def test_the_release_writer_satisfies_the_writer_port(tmp_path: Path) -> None:
    # Given an approved entity-type candidate and a writable tree
    writer: OntologyReleaseWriterPort = FilesystemOntologyReleaseWriter()
    root = _ontology_root(tmp_path)
    candidate = OntologyDiscoveryCandidate(
        domain_profile="empty",
        kind="entity_type",
        symbol="contract",
        label="계약",
        definition="업무상 체결하는 계약을 표현한다.",
        fingerprint="sha256:" + "0" * 64,
    )

    # When it is materialized through the port
    release = writer.materialize(root, "empty", candidate)

    # Then the release lands in the domain profile the candidate names
    assert release.kind == "entity_type"
    assert release.symbol == "contract"
    assert release.file == "domains/empty.yaml"
    assert release.catalog_refresh == "restart_required"


def test_the_release_writer_names_the_journal_doctor_reports(tmp_path: Path) -> None:
    # Given a tree with no crashed release
    writer: OntologyReleaseWriterPort = FilesystemOntologyReleaseWriter()
    root = _ontology_root(tmp_path)

    # When doctor asks the writer where a crash journal would be
    journal = writer.pending_release_journal(root)

    # Then it is the writer's own journal path, and it does not exist
    assert journal == root / RELEASE_JOURNAL_FILENAME
    assert not journal.exists()
