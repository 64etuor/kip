from __future__ import annotations

from kip.domain.models import SearchHit
from kip.ports.reranker import RerankScore


def reciprocal_rank_fusion(
    lexical: list[SearchHit],
    vector: list[SearchHit],
    *,
    limit: int,
    rank_constant: int = 60,
) -> list[SearchHit]:
    by_unit: dict[str, SearchHit] = {}
    scores: dict[str, float] = {}
    metadata: dict[str, dict] = {}
    for channel, hits in (("lexical", lexical), ("vector", vector)):
        for rank, hit in enumerate(hits, start=1):
            if hit.unit_id not in by_unit or channel == "lexical":
                by_unit[hit.unit_id] = hit
            scores[hit.unit_id] = scores.get(hit.unit_id, 0.0) + 1.0 / (
                rank_constant + rank
            )
            state = metadata.setdefault(
                hit.unit_id,
                {
                    "retrieval_channels": [],
                    "lexical_rank": None,
                    "vector_rank": None,
                    "lexical_score": None,
                    "vector_score": None,
                },
            )
            state["retrieval_channels"].append(channel)
            state[f"{channel}_rank"] = rank
            state[f"{channel}_score"] = hit.score

    fused = [
        hit.model_copy(
            update={
                "score": scores[unit_id],
                "metadata": {**hit.metadata, **metadata[unit_id]},
            },
            deep=True,
        )
        for unit_id, hit in by_unit.items()
    ]
    fused.sort(
        key=lambda hit: (
            -hit.score,
            hit.metadata["lexical_rank"] or 10**9,
            hit.metadata["vector_rank"] or 10**9,
            hit.unit_id,
        )
    )
    return fused[:limit]


def apply_rerank(
    hits: list[SearchHit],
    scores: list[RerankScore],
    *,
    limit: int,
) -> list[SearchHit]:
    result: list[SearchHit] = []
    for rank, score in enumerate(scores[:limit], start=1):
        hit = hits[score.index]
        result.append(
            hit.model_copy(
                update={
                    "score": score.score,
                    "metadata": {
                        **hit.metadata,
                        "rrf_score": hit.score,
                        "rerank_score": score.score,
                        "rerank_rank": rank,
                    },
                },
                deep=True,
            )
        )
    return result

