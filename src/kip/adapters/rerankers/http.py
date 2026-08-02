from __future__ import annotations

import math
from collections.abc import Sequence

import httpx

from kip.adapters.embeddings.http import require_allowed_model_url
from kip.errors import DependencyUnavailableError
from kip.ports.reranker import RerankScore


class HttpRerankerAdapter:
    name = "http"
    provider = "infinity"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        revision: str,
        allow_remote_egress: bool = False,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = require_allowed_model_url(base_url, allow_remote_egress)
        self.model = model
        self.revision = revision
        self.client = client or httpx.Client(timeout=timeout_seconds)

    def rerank(self, query: str, documents: Sequence[str]) -> list[RerankScore]:
        if not documents:
            return []
        try:
            response = self.client.post(
                f"{self.base_url}/rerank",
                json={
                    "model": self.model,
                    "query": query,
                    "documents": list(documents),
                    "return_documents": False,
                },
            )
            response.raise_for_status()
            rows = response.json()["results"]
            scores = [
                RerankScore(index=int(row["index"]), score=float(row["relevance_score"]))
                for row in rows
            ]
            if {score.index for score in scores} != set(range(len(documents))):
                raise ValueError("reranker response indexes do not match input documents")
            if any(not math.isfinite(score.score) for score in scores):
                raise ValueError("reranker response contains non-finite values")
            return sorted(scores, key=lambda score: (-score.score, score.index))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise DependencyUnavailableError(
                f"reranking model service is unavailable: {error}"
            ) from error
