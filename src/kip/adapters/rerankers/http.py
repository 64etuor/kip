from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence

import httpx

from kip.adapters.embeddings.http import ServedModelGuard, require_allowed_model_url
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
        max_document_chars: int = 2048,
        model_service_hosts: Sequence[str] = (),
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_document_chars < 100:
            raise ValueError("reranker max_document_chars must be at least 100")
        self.base_url = require_allowed_model_url(base_url, allow_remote_egress, model_service_hosts)
        self.model = model
        self.revision = revision
        # A cross-encoder reads a bounded window per candidate; sending whole
        # 12k-character units only adds transfer and tokenization latency.
        self.max_document_chars = max_document_chars
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds))
        # A one-line `/models` answer needs none of the rerank budget, and a
        # stalled runtime must not burn it twice (probe, then `/rerank`).
        self._probe_timeout = httpx.Timeout(
            min(5.0, timeout_seconds), connect=min(3.0, timeout_seconds)
        )
        self.client = client or httpx.Client(
            timeout=self._timeout,
            trust_env=False,
        )
        self._served_models = ServedModelGuard(
            client=self.client,
            base_url=self.base_url,
            model=self.model,
            role="reranking",
            setting="models.reranker.model",
            clock=clock,
        )

    def rerank(self, query: str, documents: Sequence[str]) -> list[RerankScore]:
        if not documents:
            return []
        self._served_models.require(timeout=self._probe_timeout)
        try:
            response = self.client.post(
                f"{self.base_url}/rerank",
                json={
                    "model": self.model,
                    "query": query,
                    "documents": [document[: self.max_document_chars] for document in documents],
                    "return_documents": False,
                },
                timeout=self._timeout,
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
