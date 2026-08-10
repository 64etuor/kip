from __future__ import annotations

import math
from collections.abc import Sequence
from importlib import import_module
from typing import Protocol, cast

from kip.errors import ConfigurationError, DependencyUnavailableError
from kip.ports.reranker import RerankScore


class JinaSequenceClassifier(Protocol):
    def compute_score(
        self,
        pairs: Sequence[Sequence[str]],
        *,
        max_length: int,
    ) -> Sequence[float]: ...

    def eval(self) -> JinaSequenceClassifier: ...

    def to(self, device: str) -> JinaSequenceClassifier: ...


class JinaSequenceClassifierFactory(Protocol):
    def from_pretrained(self, model: str, **kwargs: object) -> JinaSequenceClassifier: ...


class HuggingFaceJinaRerankerAdapter:
    """Run a pinned Jina multilingual reranker through the Hugging Face model API."""

    name = "huggingface"
    provider = "jinaai"

    def __init__(
        self,
        *,
        model: str,
        revision: str,
        max_length: int = 1024,
        device: str | None = None,
        model_instance: JinaSequenceClassifier | None = None,
    ) -> None:
        if max_length < 1:
            raise ConfigurationError("Jina reranker max_length must be positive")
        self.model = model
        self.revision = revision
        self.max_length = max_length
        selected = (
            model_instance
            if model_instance is not None
            else self._load_model(model, revision)
        )
        if device:
            selected = selected.to(device)
        self._model = selected.eval()

    @staticmethod
    def _load_model(model: str, revision: str) -> JinaSequenceClassifier:
        try:
            factory = cast(
                JinaSequenceClassifierFactory,
                import_module("transformers").AutoModelForSequenceClassification,
            )
        except ModuleNotFoundError as error:
            raise DependencyUnavailableError(
                "Hugging Face Jina reranking requires the semantic dependencies"
            ) from error
        try:
            return factory.from_pretrained(
                model,
                revision=revision,
                dtype="auto",
                trust_remote_code=True,
                use_flash_attn=False,
            )
        except (OSError, RuntimeError) as error:
            raise DependencyUnavailableError(
                f"Jina reranker model is unavailable: {model}@{revision}"
            ) from error

    def rerank(self, query: str, documents: Sequence[str]) -> list[RerankScore]:
        if not documents:
            return []
        pairs = [[query, document] for document in documents]
        try:
            raw_scores = self._model.compute_score(pairs, max_length=self.max_length)
            scores = [float(score) for score in raw_scores]
        except (RuntimeError, TypeError, ValueError) as error:
            raise DependencyUnavailableError(f"Jina reranker failed: {error}") from error
        if len(scores) != len(documents):
            raise DependencyUnavailableError(
                "Jina reranker response count does not match input documents"
            )
        if any(not math.isfinite(score) for score in scores):
            raise DependencyUnavailableError("Jina reranker response contains non-finite values")
        return sorted(
            [RerankScore(index=index, score=score) for index, score in enumerate(scores)],
            key=lambda score: (-score.score, score.index),
        )
