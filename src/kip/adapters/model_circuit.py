"""Fail fast while a local model runtime is down.

The embedding and reranker sidecar is optional at query time: default-mode
search degrades to the lexical path when it is unavailable. Without a breaker
every query would wait for a connection error or timeout first. After one
failure the guarded adapter raises immediately until the cooldown passes,
then lets a single call probe the runtime again.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from typing import TypeVar

from kip.errors import DependencyUnavailableError
from kip.ports.embedding import EmbeddingPort
from kip.ports.reranker import RerankerPort, RerankScore

T = TypeVar("T")


class ModelCircuit:
    """Closed until a failure; then open for the cooldown; then one probe.

    While open, calls fail immediately. After the cooldown exactly one caller
    probes the runtime (half-open) while concurrent callers keep failing
    fast; the probe's outcome closes or re-opens the circuit. Results of
    calls that started before a failure do not close it.
    """

    def __init__(self, *, cooldown_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must not be negative")
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._open_until = 0.0
        self._probing = False
        self._generation = 0
        self._last_error = ""

    def call(self, label: str, operation: Callable[[], T]) -> T:
        with self._lock:
            now = self._clock()
            if self._generation and (now < self._open_until or self._probing):
                remaining = max(self._open_until - now, 0.0)
                raise DependencyUnavailableError(
                    f"{label} is unavailable ({self._last_error}); retrying in {remaining:.0f}s"
                )
            # Open with the cooldown over and nobody probing: this caller probes.
            probe = bool(self._generation)
            if probe:
                self._probing = True
        try:
            result = operation()
        except DependencyUnavailableError as error:
            with self._lock:
                self._generation += 1
                self._open_until = self._clock() + self._cooldown
                self._last_error = str(error)
                self._probing = False
            raise
        except BaseException:
            # Any other failure of a probe must not leave the circuit stuck.
            if probe:
                with self._lock:
                    self._probing = False
            raise
        if probe:
            # Only the probe closes an open circuit; a slow call that began
            # before a failure leaves it open.
            with self._lock:
                self._generation = 0
                self._probing = False
        return result


class GuardedEmbedding:
    def __init__(self, delegate: EmbeddingPort, circuit: ModelCircuit) -> None:
        self._delegate = delegate
        self._circuit = circuit
        self.name = delegate.name
        self.provider = delegate.provider
        self.model = delegate.model
        self.revision = delegate.revision
        self.dimensions = delegate.dimensions
        self.normalized = delegate.normalized

    def embed_query(self, text: str) -> list[float]:
        return self._circuit.call("embedding model", lambda: self._delegate.embed_query(text))

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._circuit.call("embedding model", lambda: self._delegate.embed_documents(texts))


class GuardedReranker:
    def __init__(self, delegate: RerankerPort, circuit: ModelCircuit) -> None:
        self._delegate = delegate
        self._circuit = circuit
        self.name = delegate.name
        self.provider = delegate.provider
        self.model = delegate.model
        self.revision = delegate.revision

    def rerank(self, query: str, documents: Sequence[str]) -> list[RerankScore]:
        return self._circuit.call("reranker model", lambda: self._delegate.rerank(query, documents))
