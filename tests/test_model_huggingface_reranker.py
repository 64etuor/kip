from __future__ import annotations

import pytest

from kip.adapters.repository.memory import MemoryRepository
from kip.adapters.rerankers.huggingface import HuggingFaceJinaRerankerAdapter
from kip.container import build_container
from kip.errors import DependencyUnavailableError


class FakeJinaModel:
    def __init__(self) -> None:
        self.pairs: list[list[str]] = []
        self.max_length: int | None = None

    def compute_score(self, pairs, *, max_length: int):
        self.pairs = pairs
        self.max_length = max_length
        return [0.1, 0.9]

    def eval(self):
        return self

    def to(self, device: str):
        return self


class JinaRuntimeError(RuntimeError):
    pass


class FailingJinaModel(FakeJinaModel):
    def compute_score(self, pairs, *, max_length: int):
        raise JinaRuntimeError("MPS out of memory")


def test_jina_huggingface_adapter_returns_scores_in_relevance_order() -> None:
    model = FakeJinaModel()
    adapter = HuggingFaceJinaRerankerAdapter(
        model="jinaai/jina-reranker-v2-base-multilingual",
        revision="9cfeff2df7d40d1b78e75e5e9cebec92a99813c9",
        max_length=512,
        model_instance=model,
    )

    scores = adapter.rerank("승인 근거", ["낮음", "높음"])

    assert [(score.index, score.score) for score in scores] == [(1, 0.9), (0, 0.1)]
    assert model.pairs == [["승인 근거", "낮음"], ["승인 근거", "높음"]]
    assert model.max_length == 512


def test_container_selects_huggingface_reranker_backend(test_container, monkeypatch) -> None:
    model = FakeJinaModel()
    monkeypatch.setattr(
        HuggingFaceJinaRerankerAdapter,
        "_load_model",
        staticmethod(lambda model, revision: model_instance),
    )
    model_instance = model
    test_container.settings.raw["models"] = {
        "reranker": {
            "enabled": True,
            "backend": "huggingface",
            "model": "jinaai/jina-reranker-v2-base-multilingual",
            "revision": "9cfeff2df7d40d1b78e75e5e9cebec92a99813c9",
        }
    }

    container = build_container(test_container.settings, repository=MemoryRepository())

    assert isinstance(container.reranker, HuggingFaceJinaRerankerAdapter)


def test_jina_model_runtime_failure_is_dependency_unavailable() -> None:
    adapter = HuggingFaceJinaRerankerAdapter(
        model="jinaai/jina-reranker-v2-base-multilingual",
        revision="9cfeff2df7d40d1b78e75e5e9cebec92a99813c9",
        model_instance=FailingJinaModel(),
    )

    with pytest.raises(DependencyUnavailableError, match="Jina reranker failed"):
        adapter.rerank("질의", ["문서"])
