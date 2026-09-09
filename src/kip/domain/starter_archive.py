from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class StarterArchiveModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StarterArchiveSource(StarterArchiveModel):
    git_commit: str
    tracked_changes: bool
    repository: str | None = None


class StarterArchiveManifest(StarterArchiveModel):
    schema_version: Literal["kip.starter-archive.v1"] = "kip.starter-archive.v1"
    version: str
    created_at: datetime
    root: str
    files: dict[str, str]
    source: StarterArchiveSource


class StarterArchiveReceipt(StarterArchiveModel):
    schema_version: Literal["kip.starter-archive-receipt.v1"] = (
        "kip.starter-archive-receipt.v1"
    )
    archive: str
    archive_sha256: str
    file_count: int
    root: str
    status: Literal["built", "verified"]
    version: str
