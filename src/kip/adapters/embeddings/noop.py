class DisabledEmbeddingAdapter:
    name = "disabled"
    provider = "disabled"
    model = "disabled"
    revision = "disabled"
    dimensions = 0
    normalized = False

    def embed_query(self, text):
        raise RuntimeError("semantic search is disabled")

    def embed_documents(self, texts):
        raise RuntimeError("semantic search is disabled")
