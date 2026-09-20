"""The filesystem side of `ontology/`, as seen by the use cases.

`ontology/` owns meaning and PostgreSQL owns state, but the meaning is carried
by a YAML tree on disk. What that tree *says* is domain data
(:mod:`kip.domain.ontology`); reading it, diffing two of them and writing an
approved release into one are filesystem operations, and these are the only
two ways the application layer reaches them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from kip.domain.interactions import (
    OntologyDiscoveryCandidate,
    OntologyDiscoveryRelease,
)
from kip.domain.ontology import OntologyCatalog
from kip.domain.ontology_migration import OntologyDiff


class OntologyCatalogPort(Protocol):
    """Reads released ontology trees."""

    def load(
        self,
        root: Path,
        *,
        domain_profile: str,
    ) -> OntologyCatalog:
        """The vocabulary released by the tree at `root`.

        Raises :class:`kip.errors.ValidationError` when the tree fails
        contract validation, so a use case never sees a half-valid catalog.
        """
        ...

    def diff(
        self,
        before_root: Path,
        after_root: Path,
        *,
        domain_profile: str,
    ) -> OntologyDiff:
        """Classify every change between two released trees."""
        ...


class OntologyReleaseWriterPort(Protocol):
    """Writes an approved discovery candidate into the ontology tree."""

    def materialize(
        self,
        ontology_root: Path,
        domain_profile: str,
        candidate: OntologyDiscoveryCandidate,
    ) -> OntologyDiscoveryRelease:
        """Release `candidate` into the tree, or return the existing release.

        Shadow-validated and atomic: on failure no real file under
        `ontology_root` is modified, so the caller may persist the approval
        only after this returns. Raises :class:`kip.errors.ValidationError`
        when the tree is read-only, the release would fail contract
        validation, the release lock cannot be acquired, or the candidate kind
        is not releasable automatically; :class:`kip.errors.ConflictError`
        when the symbol was already released with different content.
        """
        ...

    def pending_release_journal(self, ontology_root: Path) -> Path:
        """Where a crashed release leaves its recovery journal.

        `kip doctor` reports whether this file exists; the path shape is the
        writer's own crash-safety detail, so the use case asks for it instead
        of knowing the filename.
        """
        ...
