from __future__ import annotations

import math
from pathlib import Path

from kip.adapters.repository.memory import MemoryRepository
from kip.adapters.rerankers.rapidfuzz import RapidFuzzRerankerAdapter
from kip.container import build_container
from kip.settings import Settings


def test_rapidfuzz_reranker_recovers_a_korean_typo_locally() -> None:
    # Given two bounded evidence units and a misspelled Korean query.
    adapter = RapidFuzzRerankerAdapter(max_document_chars=8000)
    documents = [
        "일반 계약 서류와 비용 정산 안내",
        "협약 변경 승인 절차와 제출 서류",
    ]

    # When the local deterministic adapter reranks them.
    scores = adapter.rerank("협약 변겅 승인", documents)

    # Then the typo-near evidence wins with finite, versioned scores.
    assert scores[0].index == 1
    assert all(math.isfinite(score.score) for score in scores)
    assert adapter.provider == "rapidfuzz"
    assert adapter.revision == "3.14.5"


def test_container_composes_rapidfuzz_behind_the_reranker_port(
    tmp_path: Path,
) -> None:
    # Given a starter configuration selecting the local reranker backend.
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "search": {"semantic_enabled": False, "lexical_rerank_enabled": True},
            "models": {
                "reranker": {
                    "enabled": True,
                    "backend": "rapidfuzz",
                    "max_document_chars": 8000,
                }
            },
            "parsers": {"hwp": {"order": ["paired_pdf"]}},
        },
        environment="test",
        workspace="default",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )

    # When the application container is built without model sidecars.
    container = build_container(
        settings,
        repository=MemoryRepository(),
        load_models=False,
    )

    # Then a local RapidFuzz adapter still satisfies the reranker port.
    assert isinstance(container.reranker, RapidFuzzRerankerAdapter)
