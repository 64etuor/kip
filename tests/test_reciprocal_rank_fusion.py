from __future__ import annotations

from kip.application.retrieval import reciprocal_rank_fusion
from kip.domain.models import EvidenceLocator, SearchHit


def _hit(unit_id: str, score: float) -> SearchHit:
    return SearchHit(
        unit_id=unit_id,
        document_id=f"doc_{unit_id}",
        artifact_id=f"art_{unit_id}",
        source_kind="filesystem",
        title=unit_id,
        snippet=unit_id,
        score=score,
        locator=EvidenceLocator(type="text_span", data={}),
        source_uri=f"file:///public/{unit_id}.txt",
        source_sha256="a" * 64,
    )


def test_rrf_uses_rank_and_deduplicates_units() -> None:
    lexical = [_hit("a", 100.0), _hit("b", 90.0)]
    vector = [_hit("b", 0.99), _hit("c", 0.98)]

    fused = reciprocal_rank_fusion(lexical, vector, limit=3, rank_constant=60)

    assert [hit.unit_id for hit in fused] == ["b", "a", "c"]
    assert fused[0].metadata["retrieval_channels"] == ["lexical", "vector"]
    assert fused[0].metadata["lexical_rank"] == 2
    assert fused[0].metadata["vector_rank"] == 1
    assert fused[0].score < 1.0
