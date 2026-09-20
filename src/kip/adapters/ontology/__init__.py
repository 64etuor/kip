"""Filesystem adapters for the ontology tree."""

from kip.adapters.ontology.catalog import FilesystemOntologyCatalog
from kip.adapters.ontology.release import FilesystemOntologyReleaseWriter

__all__ = ["FilesystemOntologyCatalog", "FilesystemOntologyReleaseWriter"]
