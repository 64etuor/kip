from __future__ import annotations

import ipaddress
import math
from collections.abc import Sequence
from urllib.parse import urlparse

import httpx

from kip.errors import ConfigurationError, DependencyUnavailableError


def require_allowed_model_url(base_url: str, allow_remote_egress: bool) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError("model base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ConfigurationError("model base URL must not contain credentials")
    if allow_remote_egress:
        return base_url.rstrip("/")
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = parsed.hostname.lower() == "localhost"
    if not loopback:
        raise ConfigurationError(
            "model base URL must be loopback while remote model egress is disabled"
        )
    return base_url.rstrip("/")


class HttpEmbeddingAdapter:
    name = "http"
    provider = "infinity"
    normalized = True

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        revision: str,
        dimensions: int,
        query_instruction: str = "",
        allow_remote_egress: bool = False,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = require_allowed_model_url(base_url, allow_remote_egress)
        self.model = model
        self.revision = revision
        self.dimensions = dimensions
        self.query_instruction = query_instruction
        self.client = client or httpx.Client(
            timeout=timeout_seconds,
            trust_env=False,
        )

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self.query_instruction + text])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed(list(texts))

    def _embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self.client.post(
                f"{self.base_url}/embeddings",
                json={"model": self.model, "input": texts},
            )
            response.raise_for_status()
            rows = response.json()["data"]
            ordered = sorted(rows, key=lambda row: int(row["index"]))
            embeddings = [
                [float(value) for value in row["embedding"]]
                for row in ordered
            ]
            if len(embeddings) != len(texts):
                raise ValueError("embedding response count does not match input count")
            if any(len(embedding) != self.dimensions for embedding in embeddings):
                raise ValueError(
                    f"embedding dimension does not match configured {self.dimensions}"
                )
            if any(not math.isfinite(value) for embedding in embeddings for value in embedding):
                raise ValueError("embedding response contains non-finite values")
            return embeddings
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise DependencyUnavailableError(
                f"embedding model service is unavailable: {error}"
            ) from error
