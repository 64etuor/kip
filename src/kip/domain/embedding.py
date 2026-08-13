from __future__ import annotations

from pydantic import Field

from kip.domain.models import StrictModel


class EmbeddingProjectionProgress(StrictModel):
    content_units: int = Field(ge=0)
    indexed_units: int = Field(ge=0)
