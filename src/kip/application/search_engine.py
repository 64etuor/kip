from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Final

from kip.application.retrieval import apply_rerank, reciprocal_rank_fusion
from kip.application.semantic import SEMANTIC_DEFAULT_MODE, SemanticProjectionUseCases
from kip.domain.file_references import FilenameSearchRequest
from kip.domain.knowledge import normalize_entity_name
from kip.domain.models import ContentUnit, RequestContext, SearchHit, SearchRequest
from kip.domain.text import normalize_text, strip_inline_markup
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


# Fallbacks for `[search]` keys the shipped profile sets. A fallback that
# disagrees with `config/kip.example.toml` makes a config that omits the key
# behave unlike the documented profile, so they live in one mapping and
# `tests/test_shipped_defaults_parity.py` pins them to the shipped values.
# `semantic_enabled` is deliberately absent: semantic retrieval needs a model
# runtime, so it stays opt-in rather than defaulting to the shipped profile.
SEARCH_DEFAULTS: Final[dict[str, object]] = {
    "default_mode": SEMANTIC_DEFAULT_MODE,
    "context_item_max_chars": 16000,
    "alias_expansion_enabled": True,
    "alias_expansion_max_terms": 16,
    "max_hits_per_document": 3,
    "abstain_on_unknown_terms": True,
    "hybrid_candidate_limit": 40,
    "rerank_candidate_limit": 40,
    "lexical_rerank_enabled": True,
    "lexical_rerank_candidate_limit": 40,
    "rrf_rank_constant": 60,
}


@dataclass(frozen=True, slots=True)
class RankedHits:
    """A ranked result together with the degradations that produced it.

    Degradation used to be readable only from hit metadata, which silently
    vanished whenever the degraded pool came back empty. Reporting it beside
    the hits keeps the signal independent of how many results survived.
    """

    hits: list[SearchHit]
    degraded: tuple[str, ...] = ()


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
_PARTICLES = ("에서는", "으로는", "에게는", "에서", "으로", "에게", "까지", "부터", "의", "은", "는", "이", "가", "을", "를", "에", "로", "와", "과", "도")


def _content_terms(text: str) -> list[str]:
    tokens = _CONTENT_TOKEN_RE.findall(normalize_text(text).casefold())
    terms = list(tokens)
    for token in tokens:
        if not all("가" <= char <= "힣" for char in token):
            continue
        for suffix in _PARTICLES:
            if token.endswith(suffix) and len(token) - len(suffix) >= 2:
                terms.append(token[:-len(suffix)])
                break
    # These are only candidates for the ACL-scoped vocabulary check. An
    # inferred stem cannot permit retrieval unless it exists in the corpus.
    return list(dict.fromkeys(terms))


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
        *,
        lexical_reranker: RerankerPort | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._analyzer = analyzer
        self._embedding = embedding
        self._semantic = semantic
        # `reranker` scores the fused lexical+vector pool (`reranked` mode);
        # `lexical_reranker` scores lexical mode and the `semantic_degraded`
        # fallback. They differ when an opt-in model cross-encoder is
        # configured, so lexical ranking never waits on (or fails with) the
        # model runtime.
        self._reranker = reranker
        self._lexical_reranker = lexical_reranker
        self._knowledge = knowledge

    def _diversify(self, hits: list[SearchHit], limit: int) -> list[SearchHit]:
        """Cap hits per document so one file cannot fill every slot.

        Overflow hits backfill the tail when there are not enough distinct
        documents, so result count never shrinks below what the pool allows.
        """
        cap = int(
            self._settings.get(
                "search.max_hits_per_document",
                SEARCH_DEFAULTS["max_hits_per_document"],
            )
        )
        if cap <= 0:
            return hits[:limit]
        selected: list[SearchHit] = []
        overflow: list[SearchHit] = []
        counts: dict[str, int] = {}
        for hit in hits:
            if len(selected) >= limit:
                break
            document_key = hit.document_id or f"unit:{hit.unit_id}"
            seen = counts.get(document_key, 0)
            if seen < cap:
                counts[document_key] = seen + 1
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
                    update={"metadata": {**hit.metadata, "diversity_backfill": True}},
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
            self._settings.get(
                "search.alias_expansion_enabled",
                SEARCH_DEFAULTS["alias_expansion_enabled"],
            )
        ):
            return []
        normalized_query = normalize_entity_name(query)
        if not normalized_query:
            return []
        max_terms = int(
            self._settings.get(
                "search.alias_expansion_max_terms",
                SEARCH_DEFAULTS["alias_expansion_max_terms"],
            )
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
        return self.search_ranked(context, request, mode=mode).hits

    def search_ranked(
        self,
        context: RequestContext,
        request: SearchRequest,
        *,
        mode: str | None = None,
    ) -> RankedHits:
        # The pipeline is a fixed sequence of stages with one exit:
        #   plan → analyze → check content/identifiers → rank → diversify.
        # Each stage is a named method so a change in one cannot silently
        # reorder another; only the pool builder branches on mode.
        plan = self._resolve_mode(mode)
        query = self._analyze(context, request.query)
        explicitly_scoped = isinstance(request, FilenameSearchRequest) and bool(request.included_filenames)
        if not explicitly_scoped and self._should_abstain(context, query) and not self._store.has_identifier_match(
            context, request
        ):
            return RankedHits(hits=[])
        degraded: list[str] = []
        pool = self._ranked_pool(context, request, query, plan, degraded)
        return RankedHits(
            hits=self._diversify(pool, request.limit),
            degraded=tuple(degraded),
        )

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
        expansion — is absent from the reachable corpus. The caller also
        checks literal identifiers: filenames need not occur in body
        vocabulary. Any single grounded term keeps retrieval alive.
        Distinguishing partial
        nonsense from a low-overlap paraphrase, or a real-word query with
        no factual answer, needs the calibrated semantic score — which
        plugs into this same gate once the vector space is active.
        """
        if not bool(
            self._settings.get(
                "search.abstain_on_unknown_terms",
                SEARCH_DEFAULTS["abstain_on_unknown_terms"],
            )
        ):
            return False
        tokens = query.content_tokens
        if not tokens:
            return False
        candidates = list(dict.fromkeys([*tokens, *query.expansion_terms]))
        return not self._store.any_term_visible(context, candidates)

    def _resolve_mode(self, mode: str | None) -> _QueryPlan:
        explicit = mode is not None
        configured_mode = (
            str(self._settings.get("search.default_mode", SEARCH_DEFAULTS["default_mode"]))
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
        content_tokens = _content_terms(query_text)
        expansion_terms = list(
            dict.fromkeys(
                token for term in expansion for token in _content_terms(term)
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
        degraded: list[str],
    ) -> list[SearchHit]:
        if plan.mode is SearchMode.LEXICAL:
            return self._lexical_pool(context, request, query, degraded)
        return self._semantic_pool(context, request, query, plan, degraded)

    def _candidate_limit(self, request: SearchRequest, setting: str) -> int:
        return min(
            100,
            max(
                request.limit,
                int(
                    self._settings.get(
                        setting,
                        SEARCH_DEFAULTS[setting.removeprefix("search.")],
                    )
                ),
            ),
        )

    def _candidate_pool(
        self,
        context: RequestContext,
        request: SearchRequest,
        query: _AnalyzedQuery,
        candidate_limit: int,
    ) -> list[SearchHit]:
        candidate_request = request.model_copy(update={"limit": candidate_limit})
        return self._annotate_lexical(self._store.search(context, candidate_request, query.lexemes))

    def _lexical_pool(
        self,
        context: RequestContext,
        request: SearchRequest,
        query: _AnalyzedQuery,
        degraded: list[str],
    ) -> list[SearchHit]:
        reranker = self._lexical_rerank_adapter()
        if reranker is None:
            candidate_limit = self._candidate_limit(request, "search.hybrid_candidate_limit")
            return self._candidate_pool(context, request, query, candidate_limit)
        candidate_limit = self._candidate_limit(request, "search.lexical_rerank_candidate_limit")
        lexical = self._candidate_pool(context, request, query, candidate_limit)
        try:
            return self._rerank(
                context,
                request,
                lexical,
                reranker,
                candidate_limit=candidate_limit,
            )
        except DependencyUnavailableError:
            return self._degrade(lexical, "lexical_rerank_degraded", degraded)

    def _lexical_rerank_adapter(self) -> RerankerPort | None:
        """The reranker to score lexical results with, if there is one.

        The shipped profile enables lexical reranking and configures a local
        BM25 reranker, so the fallback is on. A config that sets neither has
        no adapter to call: a default must never fail a search, so lexical
        ranking is simply left unreranked. An explicit
        `lexical_rerank_enabled = true` without an adapter stays a loud
        misconfiguration, because there the operator did ask for it.
        """
        configured = self._settings.get("search.lexical_rerank_enabled")
        enabled = (
            bool(SEARCH_DEFAULTS["lexical_rerank_enabled"])
            if configured is None
            else bool(configured)
        )
        if not enabled:
            return None
        if self._lexical_reranker is None:
            if configured is None:
                return None
            raise DependencyUnavailableError(
                "lexical reranking is enabled without a reranker adapter"
            )
        return self._lexical_reranker

    def _semantic_pool(
        self,
        context: RequestContext,
        request: SearchRequest,
        query: _AnalyzedQuery,
        plan: _QueryPlan,
        degraded: list[str],
    ) -> list[SearchHit]:
        candidate_limit = self._candidate_limit(request, "search.hybrid_candidate_limit")
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
            lexical = self._candidate_pool(context, request, query, candidate_limit)
            fused = reciprocal_rank_fusion(
                lexical,
                vector,
                limit=candidate_limit,
                rank_constant=int(
                    self._settings.get(
                        "search.rrf_rank_constant",
                        SEARCH_DEFAULTS["rrf_rank_constant"],
                    )
                ),
            )
        except DependencyUnavailableError:
            if plan.explicit:
                raise
            # Fall back to exactly the lexical mode users and the private
            # gate's lexical floor measure, BM25 rerank included.
            try:
                pool = self._lexical_pool(context, request, query, degraded)
            except DependencyUnavailableError:
                # Lexical rerank was asked for explicitly but has no adapter.
                # An explicit mode still fails; this fallback must answer.
                pool = self._degrade(
                    self._candidate_pool(context, request, query, candidate_limit),
                    "lexical_rerank_degraded",
                    degraded,
                )
            return self._degrade(pool, "semantic_degraded", degraded)
        if plan.mode is SearchMode.HYBRID:
            return fused
        try:
            return self._rerank(context, request, fused, self._reranker)
        except DependencyUnavailableError:
            # Only the reranker failed: keep the fused lexical+vector ranking
            # rather than discarding the vector channel as well.
            if plan.explicit:
                raise
            return self._degrade(fused, "rerank_degraded", degraded)

    @staticmethod
    def _degrade(
        hits: list[SearchHit],
        flag: str,
        degraded: list[str],
    ) -> list[SearchHit]:
        """Record a degradation on the result and on the run that produced it.

        The metadata copy stays for per-hit inspection; `degraded` is what
        survives when the degraded pool holds no hits at all.
        """
        if flag not in degraded:
            degraded.append(flag)
        return SearchEngine._mark(hits, flag)

    @staticmethod
    def _mark(hits: list[SearchHit], flag: str, value: object = True) -> list[SearchHit]:
        return [
            hit.model_copy(
                update={"metadata": {**hit.metadata, flag: value}},
                deep=True,
            )
            for hit in hits
        ]

    def _rerank(
        self,
        context: RequestContext,
        request: SearchRequest,
        fused: list[SearchHit],
        reranker: RerankerPort | None,
        *,
        candidate_limit: int | None = None,
    ) -> list[SearchHit]:
        if reranker is None:
            raise DependencyUnavailableError("reranker adapter is disabled")
        rerank_depth = min(
            len(fused),
            candidate_limit
            if candidate_limit is not None
            else int(
                self._settings.get(
                    "search.rerank_candidate_limit",
                    SEARCH_DEFAULTS["rerank_candidate_limit"],
                )
            ),
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
                    _rerank_body(rerank_units[hit.unit_id]),
                )
                if part
            )
            for hit in rerank_hits
        ]
        scores = reranker.rerank(request.query, documents)
        # Return the full ranked pool (reranked head + fused tail); the
        # caller applies per-document diversity before truncating. The head
        # records which model scored it, so traces name the right reranker.
        reranked = self._mark(
            apply_rerank(rerank_hits, scores, limit=len(rerank_hits)),
            "rerank_model",
            reranker.model,
        )
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


def _rerank_body(unit: ContentUnit) -> str:
    """Text scored by the reranker for one unit.

    pdf-inspector Markdown marks bold/italic/superscript inline
    (``**시범사업**을``), which would split words and hide the stem/suffix
    terms BM25 relies on, so its units are scored as written. Other formats
    are scored verbatim: their ``*`` or ``<b>`` characters are content.
    """
    if unit.metadata.get("source") == "pdf_inspector":
        return strip_inline_markup(unit.body)
    return unit.body
