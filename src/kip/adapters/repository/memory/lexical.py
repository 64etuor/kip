from __future__ import annotations

from dataclasses import dataclass

from kip.adapters.repository.memory.acl import unit_is_visible
from kip.adapters.repository.memory.state import MemoryState
from kip.domain.models import (
    ArtifactView,
    ContentUnit,
    EmbeddableUnit,
    RequestContext,
    SearchHit,
    SearchRequest,
    VocabularyItem,
)
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
        raw_terms = [term for term in request.query.lower().split() if term]
        lexical_terms = [term for term in lexemes.lower().split() if term]
        unique_terms = list(dict.fromkeys([*raw_terms, *lexical_terms]))
        scored: list[tuple[float, ContentUnit, ArtifactView]] = []
        for unit in self.state.units.values():
            if not unit_is_visible(self.state, unit, context):
                continue
            view = self.state.artifacts.get(unit.artifact_id)
            if not view or not view.source_object or not view.revision:
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
            haystack = (
                f"{unit.title or ''}\n{unit.lexical_text}\n"
                f"{view.artifact.file_name}"
            ).lower()
            score = self._score(
                haystack,
                request.query.lower(),
                view.artifact.file_name.lower(),
                unique_terms,
            )
            if score > 0:
                scored.append((score, unit, view))

        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [
            _search_hit(
                score,
                unit,
                view,
                raw_terms or unique_terms,
                is_latest=revision_is_latest(self.state, view),
            )
            for score, unit, view in scored[: request.limit]
        ]

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

    @staticmethod
    def _score(
        haystack: str,
        exact_query: str,
        file_name: str,
        terms: list[str],
    ) -> float:
        score = 12.0 if exact_query in haystack else 0.0
        if exact_query == file_name:
            score += 30.0
        return score + sum(
            1.0 + min(3.0, haystack.count(term) * 0.15)
            for term in terms
            if term in haystack
        )


def snippet(body: str, terms: list[str], width: int = 360) -> str:
    normalized = " ".join(body.split())
    lower = normalized.lower()
    positions = [
        lower.find(term.lower())
        for term in terms
        if term and lower.find(term.lower()) >= 0
    ]
    start = max(0, min(positions) - width // 3) if positions else 0
    text = normalized[start : start + width]
    if start:
        text = "…" + text
    if start + width < len(normalized):
        text += "…"
    return text


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
    terms: list[str],
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
        snippet=snippet(unit.body, terms),
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
