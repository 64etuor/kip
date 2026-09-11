from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from unicodedata import normalize

from kip.adapters.repository.memory.acl import unit_is_visible
from kip.adapters.repository.memory.state import MemoryState
from kip.domain.file_references import (
    FilenameSearchRequest,
    candidate_basenames,
    filename_key,
    looks_like_file_request,
)
from kip.domain.models import (
    ArtifactView,
    ContentUnit,
    EmbeddableUnit,
    RequestContext,
    SearchHit,
    SearchRequest,
    VocabularyItem,
)
from kip.domain.snippets import discovery_snippet
from kip.errors import ValidationError


@dataclass(frozen=True, slots=True)
class MemoryLexicalStore:
    state: MemoryState

    def search(
        self,
        context: RequestContext,
        request: SearchRequest,
        lexemes: str,
    ) -> list[SearchHit]:
        normalized_query = normalize("NFC", request.query).lower()
        raw_terms = [term for term in normalized_query.split() if term]
        lexical_terms = [term for term in lexemes.lower().split() if term]
        unique_terms = list(dict.fromkeys([*raw_terms, *lexical_terms]))
        scored: list[tuple[float, ContentUnit, ArtifactView]] = []
        for unit, view in self._visible_units(context, request):
            haystack = normalize(
                "NFC", f"{unit.title or ''}\n{unit.lexical_text}\n{_identifier_text(view)}"
            ).lower()
            score = self._score(
                haystack,
                normalized_query,
                normalize("NFC", view.artifact.file_name).lower(),
                unique_terms,
            )
            if score > 0 or (isinstance(request, FilenameSearchRequest) and request.included_filenames):
                scored.append((score, unit, view))

        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [
            _search_hit(
                score,
                unit,
                view,
                request.query,
                is_latest=revision_is_latest(self.state, view),
            )
            for score, unit, view in scored[: request.limit]
        ]

    def has_identifier_match(self, context: RequestContext, request: SearchRequest) -> bool:
        needle = normalize("NFC", request.query).lower()
        return bool(needle) and any(
            needle in normalize("NFC", _identifier_text(view)).lower()
            for _, view in self._visible_units(context, request)
        )

    def has_ambiguous_filename(self, context: RequestContext, request: SearchRequest) -> bool:
        # Logical documents key on the relative path, so two sources holding
        # the same name share one document. Distinct content is the ambiguity
        # that matters; identical copies answer identically wherever cited.
        needle = normalize("NFC", request.query.strip()).casefold()
        contents: set[str] = set()
        for _unit, view in self._visible_units(context, request):
            if view.source_object is None or view.source_object.system_kind != "filesystem":
                continue
            if normalize("NFC", view.artifact.file_name).casefold() == needle:
                contents.add(view.artifact.sha256)
                if len(contents) > 1:
                    return True
        return False

    def filename_candidates(self, context: RequestContext, request: SearchRequest) -> list[str]:
        # Same contract as PostgreSQL: exact casefolded basenames spelled in
        # the question, current revision only, filesystem sources only.
        spellings = set(candidate_basenames(request.query))
        query = filename_key(request.query)
        views = [
            view
            for _, view in self._visible_units(context, request)
            if view.source_object and view.source_object.system_kind == "filesystem"
            and revision_is_latest(self.state, view)
        ]
        names = {view.artifact.file_name for view in views if filename_key(view.artifact.file_name) in spellings}
        if not names and looks_like_file_request(request.query):
            # Names outside the spelling window (very long or unusually
            # punctuated) still bind through containment.
            names = {view.artifact.file_name for view in views if filename_key(view.artifact.file_name) in query}
        return sorted(names)

    def has_visible_units(self, context: RequestContext) -> bool:
        for unit in self.state.units.values():
            if not unit_is_visible(self.state, unit, context):
                continue
            view = self.state.artifacts.get(unit.artifact_id)
            if not view or not view.revision:
                continue
            packet = self.state.packets_by_revision.get(view.revision.id)
            if packet and packet.workspace_id == context.workspace:
                return True
        return False

    def _visible_units(
        self, context: RequestContext, request: SearchRequest
    ) -> Iterator[tuple[ContentUnit, ArtifactView]]:
        for unit in self.state.units.values():
            if not unit_is_visible(self.state, unit, context):
                continue
            view = self.state.artifacts.get(unit.artifact_id)
            if not view or not view.source_object or not view.revision:
                continue
            if isinstance(request, FilenameSearchRequest) and not request.allows(
                view.artifact.file_name, view.source_object.system_kind,
            ):
                continue
            packet = self.state.packets_by_revision.get(view.revision.id)
            if not packet or packet.workspace_id != context.workspace:
                continue
            if (
                request.source_kinds
                and view.source_object.system_kind not in request.source_kinds
            ):
                continue
            document_type = view.document.document_type if view.document else None
            if request.document_types and document_type not in request.document_types:
                continue
            project_id = (
                view.document.metadata.get("project_id") if view.document else None
            )
            if request.project_ids and project_id not in request.project_ids:
                continue
            yield unit, view

    def list_embeddable_units(
        self,
        context: RequestContext,
    ) -> list[EmbeddableUnit]:
        result: list[EmbeddableUnit] = []
        for unit in self.state.units.values():
            if not unit_is_visible(self.state, unit, context):
                continue
            view = self.state.artifacts.get(unit.artifact_id)
            if not view or not view.revision:
                continue
            packet = self.state.packets_by_revision.get(view.revision.id)
            if not packet or packet.workspace_id != context.workspace:
                continue
            result.append(
                EmbeddableUnit(
                    unit_id=unit.id,
                    document_id=unit.document_id,
                    title=unit.title
                    or (view.document.title if view.document else ""),
                    body_normalized=unit.body_normalized,
                    source_hash=view.revision.sha256,
                )
            )
        return sorted(result, key=lambda item: item.unit_id)

    def vocabulary(
        self,
        context: RequestContext,
        prefix: str,
        limit: int = 20,
    ) -> list[VocabularyItem]:
        needle = prefix.strip().lower()
        if not needle:
            raise ValidationError("vocabulary prefix must not be blank")
        if len(needle.split()) > 1:
            raise ValidationError("vocabulary prefix must be a single term")
        counts: dict[str, tuple[int, int]] = {}
        for unit in self.state.units.values():
            if not unit_is_visible(self.state, unit, context):
                continue
            per_document: set[str] = set()
            for token in unit.lexical_text.lower().split():
                if not token.startswith(needle):
                    continue
                documents, corpus = counts.get(token, (0, 0))
                counts[token] = (documents, corpus + 1)
                per_document.add(token)
            for token in per_document:
                documents, corpus = counts[token]
                counts[token] = (documents + 1, corpus)
        items = [
            VocabularyItem(
                term=term,
                document_frequency=values[0],
                corpus_frequency=values[1],
            )
            for term, values in counts.items()
        ]
        items.sort(
            key=lambda item: (
                -item.document_frequency,
                -item.corpus_frequency,
                item.term,
            )
        )
        return items[:limit]

    def term_document_frequencies(
        self,
        context: RequestContext,
        terms: list[str],
    ) -> dict[str, int]:
        cleaned = [term for term in dict.fromkeys(terms) if term]
        if not cleaned:
            return {}
        documents: dict[str, set[str | None]] = {term: set() for term in cleaned}
        for unit in self.state.units.values():
            if not unit_is_visible(self.state, unit, context):
                continue
            tokens = set(unit.lexical_text.lower().split())
            for term in cleaned:
                if term.lower() in tokens:
                    documents[term].add(unit.document_id)
        return {term: len(docs) for term, docs in documents.items()}

    @staticmethod
    def _score(
        haystack: str,
        exact_query: str,
        file_name: str,
        terms: list[str],
    ) -> float:
        score = 12.0 if exact_query in haystack else 0.0
        if exact_query in file_name:
            score += 30.0
        return score + sum(
            1.0 + min(3.0, haystack.count(term) * 0.15)
            for term in terms
            if term in haystack
        )


def _identifier_text(view: ArtifactView) -> str:
    return " ".join(filter(None, [
        view.artifact.file_name,
        view.document.title if view.document else "",
        str(view.source_object.metadata.get("document_number", "")) if view.source_object else "",
        str(view.document.metadata.get("project_id", "")) if view.document else "",
    ]))


def revision_is_latest(state: MemoryState, view: ArtifactView) -> bool:
    if (
        view.document is None
        or view.revision is None
        or view.revision.source_modified_at is None
    ):
        return True
    latest = state.document_latest_modified(view.document.id)
    return latest is None or view.revision.source_modified_at >= latest


def _search_hit(
    score: float,
    unit: ContentUnit,
    view: ArtifactView,
    query: str,
    *,
    is_latest: bool,
) -> SearchHit:
    source_object = view.source_object
    revision = view.revision
    assert source_object is not None
    assert revision is not None
    return SearchHit(
        unit_id=unit.id,
        document_id=unit.document_id,
        artifact_id=unit.artifact_id,
        source_kind=source_object.system_kind,
        title=unit.title
        or (view.document.title if view.document else view.artifact.file_name),
        snippet=discovery_snippet(unit.body, query),
        score=round(score, 4),
        locator=unit.locator,
        source_uri=source_object.canonical_uri,
        source_sha256=revision.sha256,
        source_modified_at=revision.source_modified_at,
        metadata={
            "file_name": view.artifact.file_name,
            "document_type": view.document.document_type if view.document else None,
            "is_latest": is_latest,
        },
    )
