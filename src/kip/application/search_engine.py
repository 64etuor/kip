from __future__ import annotations

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
        explicit = mode is not None
        configured_mode = (
            str(self._settings.get("search.default_mode", "reranked"))
            if self._settings.get("search.semantic_enabled", False)
            else SearchMode.LEXICAL.value
        )
        raw_mode = mode or configured_mode
        try:
            selected_mode = SearchMode(raw_mode)
        except ValueError as exc:
            raise ValidationError(f"unsupported search mode: {raw_mode}") from exc
        lexemes = self._analyzer.analyze(request.query)
        expansion = self._alias_expansion(context, request.query)
        if expansion:
            # Expansion widens candidate retrieval only. The reranker keeps
            # scoring against the user's original wording: injecting synonyms
            # into the rerank query measurably promoted synonym-dense but
            # off-target documents on the golden set.
            lexemes = f"{lexemes} {self._analyzer.analyze(' '.join(expansion))}"
        if selected_mode is SearchMode.LEXICAL:
            lexical_rerank_enabled = bool(
                self._settings.get("search.lexical_rerank_enabled", False)
            )
            if not lexical_rerank_enabled:
                return self._annotate_lexical(
                    self._store.search(context, request, lexemes)
                )
            candidate_limit = min(
                100,
                max(
                    request.limit,
                    int(
                        self._settings.get(
                            "search.lexical_rerank_candidate_limit",
                            40,
                        )
                    ),
                ),
            )
            candidate_request = request.model_copy(
                update={"limit": candidate_limit}
            )
            lexical = self._annotate_lexical(
                self._store.search(context, candidate_request, lexemes)
            )
            if self._reranker is None:
                raise DependencyUnavailableError(
                    "lexical reranking is enabled without a reranker adapter"
                )
            try:
                return self._rerank(
                    context,
                    request,
                    lexical,
                    candidate_limit=candidate_limit,
                )
            except DependencyUnavailableError:
                return [
                    hit.model_copy(
                        update={
                            "metadata": {
                                **hit.metadata,
                                "lexical_rerank_degraded": True,
                            }
                        },
                        deep=True,
                    )
                    for hit in lexical[: request.limit]
                ]

        candidate_limit = min(
            100,
            max(
                request.limit,
                int(self._settings.get("search.hybrid_candidate_limit", 40)),
            ),
        )
        candidate_request = request.model_copy(update={"limit": candidate_limit})
        lexical = self._annotate_lexical(
            self._store.search(context, candidate_request, lexemes)
        )
        try:
            space = self._semantic.search_space(context, explicit=explicit)
            vector = self._store.vector_search(
                context,
                candidate_request,
                self._embedding.embed_query(request.query),
                space_id=space.id,
                limit=candidate_limit,
            )
            if selected_mode is SearchMode.VECTOR:
                return vector[: request.limit]
            fused = reciprocal_rank_fusion(
                lexical,
                vector,
                limit=candidate_limit,
                rank_constant=int(
                    self._settings.get("search.rrf_rank_constant", 60)
                ),
            )
            if selected_mode is SearchMode.HYBRID:
                return fused[: request.limit]
            return self._rerank(context, request, fused)
        except DependencyUnavailableError:
            if explicit:
                raise
            return [
                hit.model_copy(
                    update={
                        "metadata": {
                            **hit.metadata,
                            "semantic_degraded": True,
                        }
                    },
                    deep=True,
                )
                for hit in lexical[: request.limit]
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
        reranked = apply_rerank(rerank_hits, scores, limit=request.limit)
        if len(reranked) < request.limit:
            reranked.extend(
                fused[rerank_depth : rerank_depth + request.limit - len(reranked)]
            )
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
