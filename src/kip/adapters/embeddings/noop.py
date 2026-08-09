from collections.abc import Sequence


class DisabledEmbeddingAdapter:
    name = "disabled"
    provider = "disabled"
    model = "disabled"
    revision = "disabled"
    dimensions = 0
    normalized = False

    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("semantic search is disabled")

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("semantic search is disabled")
