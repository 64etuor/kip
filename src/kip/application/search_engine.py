from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum, unique

from kip.application.retrieval import apply_rerank, reciprocal_rank_fusion
from kip.application.semantic import SemanticProjectionUseCases
from kip.domain.knowledge import normalize_entity_name
from kip.domain.models import RequestContext, SearchHit, SearchRequest
from kip.errors import DependencyUnavailableError, ValidationError
from kip.ports.embedding import EmbeddingPort
from kip.ports.knowledge import KnowledgeStore
from kip.ports.reranker import RerankerPort
from kip.ports.retrieval import RetrievalStore
from kip.ports.text_analyzer import TextAnalyzerPort
from kip.settings import Settings


@unique
class SearchMode(StrEnum):
    LEXICAL = "lexical"
    VECTOR = "vector"
    HYBRID = "hybrid"
    RERANKED = "reranked"


@dataclass(frozen=True, slots=True)
class _QueryPlan:
    mode: SearchMode
    explicit: bool


@dataclass(frozen=True, slots=True)
class _AnalyzedQuery:
    text: str
    lexemes: str
    content_tokens: list[str] = field(default_factory=list)
    expansion_terms: list[str] = field(default_factory=list)


# Whole meaningful tokens (Korean runs and ASCII words of length >= 2), as
# opposed to the n-gram fragments the index also stores. These are what the
# abstention gate checks against corpus document frequency.
_CONTENT_TOKEN_RE = re.compile(r"[0-9A-Za-z]{2,}|[가-힣]{2,}")


class SearchEngine:
    def __init__(
        self,
        settings: Settings,
        store: RetrievalStore,
        analyzer: TextAnalyzerPort,
        embedding: EmbeddingPort,
        semantic: SemanticProjectionUseCases,
        reranker: RerankerPort | None = None,
        knowledge: KnowledgeStore | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._analyzer = analyzer
        self._embedding = embedding
        self._semantic = semantic
        self._reranker = reranker
        self._knowledge = knowledge

    def _diversify(self, hits: list[SearchHit], limit: int) -> list[SearchHit]:
        """Cap hits per document so one file cannot fill every slot.

        Overflow hits backfill the tail when there are not enough distinct
        documents, so result count never shrinks below what the pool allows.
        """
        cap = int(self._settings.get("search.max_hits_per_document", 3))
        if cap <= 0:
            return hits[:limit]
        selected: list[SearchHit] = []
        overflow: list[SearchHit] = []
        counts: dict[str, int] = {}
        for hit in hits:
            if len(selected) >= limit:
                break
            seen = counts.get(hit.document_id, 0)
            if seen < cap:
                counts[hit.document_id] = seen + 1
                selected.append(hit)
            else:
                overflow.append(hit)
        for hit in overflow:
            if len(selected) >= limit:
                break
            # Backfilling past the cap is how the result count is preserved
            # when too few distinct documents match; mark it so a caller can
            # tell a diverse result set from a padded one.
            selected.append(
                hit.model_copy(
                    update={
                        "metadata": {**hit.metadata, "diversity_backfill": True}
                    },
                    deep=True,
                )
            )
        return selected

    def _alias_expansion(
        self,
        context: RequestContext,
        query: str,
    ) -> list[str]:
        """Human-approved synonyms for entities mentioned in the query.

        Only active (reviewed) entities pass, and resolve_entities applies
        the ACL prefilter, so expansion never widens what a principal can
        see — it only adds vocabulary the reviewers have already bound to
        the same concept.
        """
        if self._knowledge is None or not bool(
            self._settings.get("search.alias_expansion_enabled", True)
        ):
            return []
        normalized_query = normalize_entity_name(query)
        if not normalized_query:
            return []
        max_terms = int(
            self._settings.get("search.alias_expansion_max_terms", 16)
        )
        terms: list[str] = []
        seen: set[str] = set()
        for entity in self._knowledge.resolve_entities(
            context,
            normalized_query,
            limit=8,
        ):
            # Expand only alias -> canonical: documents predominantly use
            # the canonical form, so adding it widens candidate recall,
            # while spraying sibling aliases has measurably diluted rank
            # precision on the golden set.
            canonical = normalize_entity_name(entity.canonical_name)
            if not canonical or canonical in normalized_query or canonical in seen:
                continue
            seen.add(canonical)
            terms.append(entity.canonical_name)
            if len(terms) >= max_terms:
                return terms
        return terms

    def search(
        self,
        context: RequestContext,
        request: SearchRequest,
        *,
        mode: str | None = None,
    ) -> list[SearchHit]:
        # The pipeline is a fixed sequence of stages with one exit:
        #   plan → analyze → build ranked pool → diversify + truncate.
        # Each stage is a named method so a change in one cannot silently
        # reorder another; only the pool builder branches on mode.
        plan = self._resolve_mode(mode)
        query = self._analyze(context, request.query)
        if self._should_abstain(context, query):
            return []
        pool = self._ranked_pool(context, request, query, plan)
        return self._diversify(pool, request.limit)

    def _should_abstain(
        self,
        context: RequestContext,
        query: _AnalyzedQuery,
    ) -> bool:
        """Return True when the query's vocabulary is absent from the corpus.

        A query whose whole content tokens never occur in the reachable
        corpus (a typo, a nonsense string, a topic that simply is not
        indexed) otherwise matches on incidental n-gram fragments and
        returns unranked noise. Abstaining here makes "no results" an
        honest signal instead of a list of score-zero documents.

        Scope is deliberately narrow: abstain only when the query's ENTIRE
        vocabulary — every content token and every approved-alias
        expansion — is absent from the reachable corpus. That is the only
        lexical threshold that never abstains a legitimate query (any
        single grounded term keeps retrieval alive), so it catches typos
        and nonsense without touching paraphrases. Distinguishing partial
        nonsense from a low-overlap paraphrase, or a real-word query with
        no factual answer, needs the calibrated semantic score — which
        plugs into this same gate once the vector space is active.
        """
        if not bool(self._settings.get("search.abstain_on_unknown_terms", True)):
            return False
        tokens = query.content_tokens
        if not tokens:
            return False
        candidates = list(dict.fromkeys([*tokens, *query.expansion_terms]))
        frequencies = self._store.term_document_frequencies(context, candidates)
        return max(frequencies.values(), default=0) == 0

    def _resolve_mode(self, mode: str | None) -> _QueryPlan:
        explicit = mode is not None
        configured_mode = (
            str(self._settings.get("search.default_mode", "reranked"))
            if self._settings.get("search.semantic_enabled", False)
            else SearchMode.LEXICAL.value
        )
        raw_mode = mode or configured_mode
        try:
            return _QueryPlan(mode=SearchMode(raw_mode), explicit=explicit)
        except ValueError as exc:
            raise ValidationError(f"unsupported search mode: {raw_mode}") from exc

    def _analyze(self, context: RequestContext, query_text: str) -> _AnalyzedQuery:
        lexemes = self._analyzer.analyze(query_text)
        expansion = self._alias_expansion(context, query_text)
        if expansion:
            # Expansion widens candidate retrieval only. The reranker keeps
            # scoring against the user's original wording: injecting synonyms
            # into the rerank query measurably promoted synonym-dense but
            # off-target documents on the golden set.
            lexemes = f"{lexemes} {self._analyzer.analyze(' '.join(expansion))}"
        content_tokens = list(
            dict.fromkeys(_CONTENT_TOKEN_RE.findall(query_text.lower()))
        )
        expansion_terms = list(
            dict.fromkeys(
                token
                for term in expansion
                for token in _CONTENT_TOKEN_RE.findall(term.lower())
            )
        )
        return _AnalyzedQuery(
            text=query_text,
            lexemes=lexemes,
            content_tokens=content_tokens,
            expansion_terms=expansion_terms,
        )

    def _ranked_pool(
        self,
        context: RequestContext,
        request: SearchRequest,
        query: _AnalyzedQuery,
        plan: _QueryPlan,
    ) -> list[SearchHit]:
        if plan.mode is SearchMode.LEXICAL:
            return self._lexical_pool(context, request, query)
        return self._semantic_pool(context, request, query, plan)

    def _candidate_limit(self, request: SearchRequest, setting: str) -> int:
        return min(
            100,
            max(request.limit, int(self._settings.get(setting, 40))),
        )

    def _candidate_pool(
        self,
        context: RequestContext,
        request: SearchRequest,
        query: _AnalyzedQuery,
        candidate_limit: int,
    ) -> list[SearchHit]:
        candidate_request = request.model_copy(update={"limit": candidate_limit})
        return self._annotate_lexical(
            self._store.search(context, candidate_request, query.lexemes)
        )

    def _lexical_pool(
        self,
        context: RequestContext,
        request: SearchRequest,
        query: _AnalyzedQuery,
    ) -> list[SearchHit]:
        if not bool(self._settings.get("search.lexical_rerank_enabled", False)):
            candidate_limit = self._candidate_limit(
                request, "search.hybrid_candidate_limit"
            )
            return self._candidate_pool(context, request, query, candidate_limit)
        candidate_limit = self._candidate_limit(
            request, "search.lexical_rerank_candidate_limit"
        )
        lexical = self._candidate_pool(context, request, query, candidate_limit)
        if self._reranker is None:
            raise DependencyUnavailableError(
                "lexical reranking is enabled without a reranker adapter"
            )
        try:
            return self._rerank(
                context, request, lexical, candidate_limit=candidate_limit
            )
        except DependencyUnavailableError:
            return self._mark(lexical, "lexical_rerank_degraded")

    def _semantic_pool(
        self,
        context: RequestContext,
        request: SearchRequest,
        query: _AnalyzedQuery,
        plan: _QueryPlan,
    ) -> list[SearchHit]:
        candidate_limit = self._candidate_limit(
            request, "search.hybrid_candidate_limit"
        )
        lexical = self._candidate_pool(context, request, query, candidate_limit)
        candidate_request = request.model_copy(update={"limit": candidate_limit})
        try:
            space = self._semantic.search_space(context, explicit=plan.explicit)
            vector = self._store.vector_search(
                context,
                candidate_request,
                self._embedding.embed_query(query.text),
                space_id=space.id,
                limit=candidate_limit,
            )
            if plan.mode is SearchMode.VECTOR:
                return vector
            fused = reciprocal_rank_fusion(
                lexical,
                vector,
                limit=candidate_limit,
                rank_constant=int(
                    self._settings.get("search.rrf_rank_constant", 60)
                ),
            )
            if plan.mode is SearchMode.HYBRID:
                return fused
            return self._rerank(context, request, fused)
        except DependencyUnavailableError:
            if plan.explicit:
                raise
            return self._mark(lexical, "semantic_degraded")

    @staticmethod
    def _mark(hits: list[SearchHit], flag: str) -> list[SearchHit]:
        return [
            hit.model_copy(
                update={"metadata": {**hit.metadata, flag: True}},
                deep=True,
            )
            for hit in hits
        ]

    def _rerank(
        self,
        context: RequestContext,
        request: SearchRequest,
        fused: list[SearchHit],
        *,
        candidate_limit: int | None = None,
    ) -> list[SearchHit]:
        if self._reranker is None:
            raise DependencyUnavailableError("reranker adapter is disabled")
        rerank_depth = min(
            len(fused),
            candidate_limit
            if candidate_limit is not None
            else int(self._settings.get("search.rerank_candidate_limit", 20)),
        )
        rerank_hits = fused[:rerank_depth]
        rerank_units = {
            unit.id: unit
            for unit in self._store.get_content_units(
                context,
                [hit.unit_id for hit in rerank_hits],
            )
        }
        documents = [
            "\n".join(
                part
                for part in (
                    hit.title,
                    rerank_units[hit.unit_id].body,
                )
                if part
            )
            for hit in rerank_hits
        ]
        scores = self._reranker.rerank(request.query, documents)
        # Return the full ranked pool (reranked head + fused tail); the
        # caller applies per-document diversity before truncating.
        reranked = apply_rerank(rerank_hits, scores, limit=len(rerank_hits))
        reranked.extend(fused[rerank_depth:])
        return reranked

    @staticmethod
    def _annotate_lexical(hits: list[SearchHit]) -> list[SearchHit]:
        return [
            hit.model_copy(
                update={
                    "metadata": {
                        **hit.metadata,
                        "retrieval_channels": ["lexical"],
                        "lexical_rank": rank,
                    }
                },
                deep=True,
            )
            for rank, hit in enumerate(hits, start=1)
        ]
