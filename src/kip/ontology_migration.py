"""Diff two ontology trees on disk and read a migration manifest.

The manifest, the diff and the coverage rules are domain data
(:mod:`kip.domain.ontology_migration`); this module only turns filesystem
paths into them.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from kip.domain.ontology_migration import (
    OntologyDiff,
    OntologyMigration,
    diff_releases,
)
from kip.errors import ValidationError
from kip.ontology_release import load_release


def diff_ontologies(
    before_root: Path,
    after_root: Path,
    *,
    before_domain_profile: str = "research-project",
    after_domain_profile: str = "research-project",
) -> OntologyDiff:
    before = load_release(before_root, domain_profile=before_domain_profile)
    after = load_release(after_root, domain_profile=after_domain_profile)
    return diff_releases(before, after)


def load_migration(path: Path) -> OntologyMigration:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValidationError(f"cannot load ontology migration: {error}") from error
    return OntologyMigration.model_validate(payload)
