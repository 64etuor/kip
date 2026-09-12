"""A config that omits a key must behave like the documented profile.

Several `settings.get(key, default)` fallbacks disagreed with the value
`config/kip.example.toml` ships, so omitting a key silently produced a
different deployment: a `-c12000-ht1` embedding space no release reviewed, a
shallower rerank depth, no lexical reranking, half the embedding batch size.
The fallbacks now live in one mapping per area and are pinned here.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from kip.application.search_engine import SEARCH_DEFAULTS
from kip.application.semantic import (
    EMBEDDING_DEFAULTS,
    RELEASE_REVIEWED_EMBEDDING_IDENTITIES,
    SemanticProjectionUseCases,
)

ROOT = Path(__file__).resolve().parents[1]


def _shipped() -> dict:
    return tomllib.loads((ROOT / "config/kip.example.toml").read_text(encoding="utf-8"))


def test_search_fallbacks_match_the_shipped_search_profile() -> None:
    shipped = _shipped()["search"]

    for key, value in SEARCH_DEFAULTS.items():
        assert shipped[key] == value, key


def test_embedding_fallbacks_match_the_shipped_embedding_profile() -> None:
    shipped = _shipped()["models"]["embedding"]

    for key, value in EMBEDDING_DEFAULTS.items():
        assert shipped[key] == value, key


def test_a_config_without_embedding_keys_builds_the_release_reviewed_space(
    test_container,
) -> None:
    class _ReviewedEmbedding:
        name = "http"
        provider = "infinity"
        model = "kip-qwen3-embedding-0.6b"
        revision = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
        dimensions = 1024
        normalized = True

        def embed_query(self, text):  # pragma: no cover - not reached
            raise AssertionError("no embedding call is needed to name a space")

        def embed_documents(self, texts):  # pragma: no cover - not reached
            raise AssertionError("no embedding call is needed to name a space")

    settings = test_container.settings
    settings.raw.pop("models", None)
    semantic = SemanticProjectionUseCases(settings, test_container.repository, _ReviewedEmbedding())
    context = test_container.application.operations.request_context()

    space = semantic.embedding_space(context)

    # The identity the release reviewed, not a -c12000-ht1 space that could
    # never auto-activate after hours of embedding.
    reviewed = RELEASE_REVIEWED_EMBEDDING_IDENTITIES[0]
    assert space.name.endswith(f"-c{reviewed.max_document_chars}-ht1")
    assert space.configuration["max_document_chars"] == str(reviewed.max_document_chars)

    settings.raw["models"] = {"embedding": dict(EMBEDDING_DEFAULTS)}
    shipped_space = SemanticProjectionUseCases(
        settings,
        test_container.repository,
        _ReviewedEmbedding(),
    ).embedding_space(context)

    # Only `space_name` differs (the shipped config sets a short base name);
    # the identity-bearing projection parameters must be identical.
    assert space.configuration["max_document_chars"] == shipped_space.configuration[
        "max_document_chars"
    ]
    assert space.configuration["document_projection"] == shipped_space.configuration[
        "document_projection"
    ]


def test_lexical_reranking_defaults_on_without_failing_an_unconfigured_deployment(
    test_container,
) -> None:
    from kip.domain.models import SearchRequest
    from kip.errors import DependencyUnavailableError

    settings = test_container.settings
    settings.raw["search"].pop("lexical_rerank_enabled", None)
    context = test_container.application.operations.request_context()
    (settings.project_root / "source" / "승인.txt").write_text("참여율 변경을 승인한다.")
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    # No reranker adapter is configured and the key is absent: the shipped
    # default must not turn every lexical search into an error.
    hits = test_container.application.retrieval.search(
        context,
        SearchRequest(query="승인"),
        mode="lexical",
    )
    assert hits and "rerank_rank" not in hits[0].metadata

    # Asking for it explicitly without an adapter stays a loud misconfiguration.
    settings.raw["search"]["lexical_rerank_enabled"] = True
    try:
        test_container.application.retrieval.search(
            context,
            SearchRequest(query="승인"),
            mode="lexical",
        )
    except DependencyUnavailableError as error:
        assert "reranker adapter" in str(error)
    else:  # pragma: no cover - the misconfiguration must be reported
        raise AssertionError("an explicit lexical rerank without an adapter must fail")
