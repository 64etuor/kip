from __future__ import annotations

import ipaddress
import math
import re
import threading
import time
from collections.abc import Callable, Sequence
from urllib.parse import urlparse

import httpx

from kip.errors import ConfigurationError, DependencyUnavailableError

# How long a successful `/models` verification is trusted. Infinity answers
# `/embeddings` and `/rerank` with HTTP 200 for a model name it does not
# serve, so the served list is the only evidence that queries are embedded in
# the space the active projection was built with. Re-verifying bounds how long
# a runtime restarted with another model (KIP_EMBEDDING_SERVED_MODEL pointing
# elsewhere, a stale unit file, a config whose model was edited without a
# restart) can answer with the wrong vectors; it is not a per-request probe.
# `GET /models` reports served names only: no revision and no weight hash, so
# other weights or another revision served under the configured name stay
# undetectable here and `models.embedding.revision` is unverified at runtime.
# `scripts/semantic-server.sh` refuses those overrides for that reason.
SERVED_MODEL_TTL_SECONDS = 300.0


def require_allowed_model_url(
    base_url: str,
    allow_remote_egress: bool,
    model_service_hosts: Sequence[str] = (),
) -> str:
    """Accept loopback, an explicitly named in-deployment model service, or
    any host when remote model egress is allowed.

    ``model_service_hosts`` (``security.model_service_hosts``) names services
    on the deployment's own private network, such as the compose ``models``
    service; it is an allowlist of bare host names, never a wildcard.
    """
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError("model base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ConfigurationError("model base URL must not contain credentials")
    if allow_remote_egress:
        return base_url.rstrip("/")
    host = parsed.hostname.lower()
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    allowed_services = set()
    for name in model_service_hosts:
        candidate = name.strip().lower()
        # Bare service names only (compose `models`): a dotted name or an IP
        # would quietly re-open the remote egress this setting keeps closed.
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", candidate):
            raise ConfigurationError(
                f"security.model_service_hosts entries must be bare service names, not {name!r}"
            )
        allowed_services.add(candidate)
    if not loopback and host not in allowed_services:
        raise ConfigurationError(
            "model base URL must be loopback or a configured security.model_service_hosts "
            "entry while remote model egress is disabled"
        )
    return base_url.rstrip("/")


class ServedModelGuard:
    """Refuse to use a runtime that does not serve the configured model.

    Shared by the embedding and reranker adapters: both talk to the same
    Infinity-style runtime, which ignores an unknown ``model`` in the request
    body and answers with whatever it loaded. Without this the answer looks
    normal (right dimensions, no error), so queries embedded in another space
    silently return wrong evidence instead of degrading to lexical.
    """

    def __init__(
        self,
        *,
        client: httpx.Client,
        base_url: str,
        model: str,
        role: str,
        setting: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._model = model
        self._role = role
        self._setting = setting
        self._clock = clock
        self._lock = threading.Lock()
        self._verified_until = 0.0

    def require(self, *, timeout: httpx.Timeout) -> None:
        """Verify the served model on the calling operation's timeout budget.

        The lock keeps one thread probing at a TTL boundary: REST runs the
        sync use cases in a threadpool against one shared adapter, and every
        waiting thread finds the refreshed deadline instead of probing again.
        """
        if self._clock() < self._verified_until:
            return
        with self._lock:
            now = self._clock()
            if now < self._verified_until:
                return
            try:
                response = self._client.get(f"{self._base_url}/models", timeout=timeout)
                response.raise_for_status()
                served = sorted(str(row["id"]) for row in response.json()["data"])
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
                # A failed probe is never cached: the runtime may come back
                # before the next call, and the circuit breaker paces retries.
                raise DependencyUnavailableError(
                    f"{self._role} model service is unavailable: {error}"
                ) from error
            if self._model not in served:
                raise DependencyUnavailableError(
                    f"{self._role} model service serves {served}, not the configured "
                    f"{self._model!r}; start the runtime with this model or fix {self._setting}"
                )
            self._verified_until = now + SERVED_MODEL_TTL_SECONDS


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
        query_timeout_seconds: float = 10.0,
        model_service_hosts: Sequence[str] = (),
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = require_allowed_model_url(base_url, allow_remote_egress, model_service_hosts)
        self.model = model
        self.revision = revision
        self.dimensions = dimensions
        self.query_instruction = query_instruction
        # Batch document embedding may take long; a query embedding is one
        # short text on the request path, so it gets its own tighter budget.
        self._document_timeout = httpx.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds))
        self._query_timeout = httpx.Timeout(
            query_timeout_seconds, connect=min(3.0, query_timeout_seconds)
        )
        self.client = client or httpx.Client(
            timeout=self._document_timeout,
            trust_env=False,
        )
        self._served_models = ServedModelGuard(
            client=self.client,
            base_url=self.base_url,
            model=self.model,
            role="embedding",
            setting="models.embedding.model",
            clock=clock,
        )

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self.query_instruction + text], timeout=self._query_timeout)[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed(list(texts), timeout=self._document_timeout)

    def _embed(self, texts: list[str], *, timeout: httpx.Timeout) -> list[list[float]]:
        # The probe rides the caller's budget: a TTL boundary reached inside a
        # long rebuild batch must not fail it on the 10-second query budget.
        self._served_models.require(timeout=timeout)
        try:
            response = self.client.post(
                f"{self.base_url}/embeddings",
                json={"model": self.model, "input": texts},
                timeout=timeout,
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
