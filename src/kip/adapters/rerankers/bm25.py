from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

from kip.errors import ConfigurationError, DependencyUnavailableError
from kip.ports.reranker import RerankScore

_TOKEN_RE = re.compile(r"[0-9A-Za-z]+|[가-힣]+")


def _tokens(text: str) -> list[str]:
    """Tokenize into ASCII words and Hangul character bigrams, keeping tf.

    Hangul runs are expanded into overlapping 2-grams so agglutinative
    suffixes do not hide shared stems; unlike the indexing analyzer, the
    stream is NOT deduplicated, which preserves term frequency for BM25.
    """
    stream: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        if raw[0] < "가":
            stream.append(raw.lower())
            continue
        if len(raw) <= 2:
            stream.append(raw)
            continue
        # Whole runs carry word-level idf (e.g. 협력업체), bigrams keep the
        # stem overlap that agglutinative suffixes would otherwise hide.
        stream.append(raw)
        stream.extend(raw[index : index + 2] for index in range(len(raw) - 1))
    return stream


class Bm25RerankerAdapter:
    """Okapi BM25 over the candidate set with candidate-local statistics.

    Scores only the documents it is given, so document frequency and average
    length are local to the retrieved candidates. This restores the tf and
    idf signals that the deduplicating n-gram lexical index cannot express,
    without any schema, extension, or model dependency.
    """

    name = "bm25"
    provider = "bm25"
    model = "okapi-char-bigram-v1"
    revision = "v1"

    def __init__(
        self,
        *,
        max_document_chars: int = 8000,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> None:
        if max_document_chars < 100:
            raise ConfigurationError("BM25 max_document_chars must be at least 100")
        if not 0.0 < k1 <= 10.0:
            raise ConfigurationError("BM25 k1 must be between 0 and 10")
        if not 0.0 <= b <= 1.0:
            raise ConfigurationError("BM25 b must be between 0 and 1")
        self.max_document_chars = max_document_chars
        self.k1 = k1
        self.b = b

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
    ) -> list[RerankScore]:
        if not documents:
            return []
        term_counts = [
            Counter(_tokens(document[: self.max_document_chars]))
            for document in documents
        ]
        lengths = [sum(counts.values()) for counts in term_counts]
        average_length = max(sum(lengths) / len(lengths), 1.0)
        total = len(documents)
        query_terms = set(_tokens(query))
        document_frequency = {
            term: sum(1 for counts in term_counts if term in counts)
            for term in query_terms
        }
        scores: list[RerankScore] = []
        for index, counts in enumerate(term_counts):
            score = 0.0
            length_norm = 1.0 - self.b + self.b * (
                lengths[index] / average_length
            )
            for term in query_terms:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                df = document_frequency[term]
                idf = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
                score += idf * (
                    frequency
                    * (self.k1 + 1.0)
                    / (frequency + self.k1 * length_norm)
                )
            scores.append(RerankScore(index=index, score=score))
        if any(not math.isfinite(score.score) for score in scores):
            raise DependencyUnavailableError(
                "BM25 reranking produced a non-finite score"
            )
        return sorted(scores, key=lambda score: (-score.score, score.index))
