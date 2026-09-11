from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PackageArchiveModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PackageArchiveSource(PackageArchiveModel):
    git_commit: str
    tracked_changes: bool
    repository: str | None = None


class PackageArchiveManifest(PackageArchiveModel):
    # Archives and installed manifests from releases before 3.10.0 carry the
    # former schema name; they stay readable, new archives use the new name.
    schema_version: Literal["kip.package-archive.v1", "kip.starter-archive.v1"] = "kip.package-archive.v1"
    version: str
    created_at: datetime
    root: str
    files: dict[str, str]
    source: PackageArchiveSource


class PackageArchiveReceipt(PackageArchiveModel):
    schema_version: Literal["kip.package-archive-receipt.v1"] = (
        "kip.package-archive-receipt.v1"
    )
    archive: str
    archive_sha256: str
    file_count: int
    root: str
    status: Literal["built", "verified"]
    version: str
