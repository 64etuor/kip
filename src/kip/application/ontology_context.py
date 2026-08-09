from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from kip.application.evidence import EvidenceUseCases
from kip.application.knowledge import KnowledgeUseCases
from kip.application.ontology_rag import OntologyRagUseCases
from kip.domain.knowledge import KnowledgeEntity
from kip.domain.models import (
    EvidenceRead,
    GraphEdge,
    GraphNeighborsRequest,
    GraphPath,
    GraphPathRequest,
    OntologyAnswerContext,
    OntologyAnswerEdge,
    OntologyAnswerEntity,
    OntologyAnswerPath,
    RequestContext,
)
from kip.errors import NotFoundError


@dataclass(frozen=True, slots=True)
class OntologyEvidenceContext:
    context: OntologyAnswerContext | None
    evidence: tuple[EvidenceRead, ...]
    had_stale_evidence: bool = False


class OntologyContextUseCases:
    def __init__(
        self,
        ontology_rag: OntologyRagUseCases,
        knowledge: KnowledgeUseCases,
        evidence: EvidenceUseCases,
        *,
        entity_limit: int = 8,
        edge_limit: int = 50,
        max_depth: int = 4,
    ) -> None:
        self._ontology_rag = ontology_rag
        self._knowledge = knowledge
        self._evidence = evidence
        self._entity_limit = entity_limit
        self._edge_limit = edge_limit
        self._max_depth = max_depth

    def build(
        self,
        context: RequestContext,
        query: str,
    ) -> OntologyEvidenceContext:
        matched = self._ontology_rag.resolve_entities(
            context,
            query,
            limit=self._entity_limit,
        )
        if not matched:
            return OntologyEvidenceContext(context=None, evidence=())
        edge_by_id: dict[str, GraphEdge] = {}
        for entity in matched:
            for edge in self._knowledge.graph_neighbors(
                context,
                GraphNeighborsRequest(
                    node_id=entity.id,
                    direction="both",
                    limit=self._edge_limit,
                    approved_only=True,
                ),
            ):
                edge_by_id.setdefault(edge.assertion_id, edge)
                if len(edge_by_id) >= self._edge_limit:
                    break
            if len(edge_by_id) >= self._edge_limit:
                break
        paths = self._paths(context, [item.id for item in matched])
        for path in paths:
            for assertion_id in path.assertion_ids:
                if assertion_id in edge_by_id:
                    continue
                try:
                    assertion = self._knowledge.get_assertion(context, assertion_id)
                except NotFoundError:
                    continue
                edge_by_id[assertion_id] = GraphEdge(
                    assertion_id=assertion.id,
                    subject_id=assertion.subject_id,
                    predicate=assertion.predicate,
                    object_entity_id=assertion.object_entity_id,
                    object_value=assertion.object_value,
                    status=assertion.status,
                    valid_from=assertion.valid_from,
                    valid_to=assertion.valid_to,
                    ontology_version=assertion.ontology_version,
                    evidence_unit_ids=assertion.evidence_unit_ids,
                )
                if len(edge_by_id) >= self._edge_limit:
                    break
            if len(edge_by_id) >= self._edge_limit:
                break
        now = datetime.now(UTC)
        evidence_by_id: dict[str, EvidenceRead] = {}
        accepted_edges: list[GraphEdge] = []
        had_stale_evidence = False
        for edge in edge_by_id.values():
            if not _is_current(edge, now) or not edge.evidence_unit_ids:
                continue
            edge_evidence: list[EvidenceRead] = []
            edge_is_stale = False
            for unit_id in edge.evidence_unit_ids:
                item = evidence_by_id.get(unit_id)
                if item is None:
                    try:
                        item = self._evidence.read_unit(context, unit_id)
                    except NotFoundError:
                        edge_is_stale = True
                        break
                if item.source_changed_since_index is not False:
                    edge_is_stale = True
                    break
                edge_evidence.append(item)
            if edge_is_stale:
                had_stale_evidence = True
                continue
            accepted_edges.append(edge)
            for item in edge_evidence:
                evidence_by_id.setdefault(item.unit.id, item)
        if not accepted_edges:
            return OntologyEvidenceContext(
                context=None,
                evidence=(),
                had_stale_evidence=had_stale_evidence,
            )
        accepted_ids = {item.assertion_id for item in accepted_edges}
        accepted_paths = [
            path
            for path in paths
            if path.assertion_ids
            and all(assertion_id in accepted_ids for assertion_id in path.assertion_ids)
        ]
        entities = self._context_entities(context, matched, accepted_edges)
        evidence_ids = list(
            dict.fromkeys(
                unit_id
                for edge in accepted_edges
                for unit_id in edge.evidence_unit_ids
            )
        )
        return OntologyEvidenceContext(
            context=OntologyAnswerContext(
                entities=[
                    OntologyAnswerEntity(
                        id=entity.id,
                        entity_type=entity.entity_type,
                        canonical_name=entity.canonical_name,
                        aliases=entity.aliases,
                    )
                    for entity in entities
                ],
                edges=[
                    OntologyAnswerEdge(
                        assertion_id=edge.assertion_id,
                        subject_id=edge.subject_id,
                        predicate=edge.predicate,
                        object_entity_id=edge.object_entity_id,
                        object_value=edge.object_value,
                        valid_from=edge.valid_from,
                        valid_to=edge.valid_to,
                        ontology_version=edge.ontology_version,
                        evidence_unit_ids=edge.evidence_unit_ids,
                    )
                    for edge in accepted_edges
                ],
                paths=[
                    OntologyAnswerPath(
                        node_ids=path.node_ids,
                        assertion_ids=path.assertion_ids,
                        predicates=path.predicates,
                        depth=path.depth,
                    )
                    for path in accepted_paths
                ],
                evidence_unit_ids=evidence_ids,
            ),
            evidence=tuple(evidence_by_id[item] for item in evidence_ids),
            had_stale_evidence=had_stale_evidence,
        )

    def _paths(
        self,
        context: RequestContext,
        entity_ids: list[str],
    ) -> list[GraphPath]:
        paths: list[GraphPath] = []
        for index, source_id in enumerate(entity_ids):
            for target_id in entity_ids[index + 1 :]:
                paths.extend(
                    self._knowledge.graph_path(
                        context,
                        GraphPathRequest(
                            from_node_id=source_id,
                            to_node_id=target_id,
                            max_depth=self._max_depth,
                            approved_only=True,
                        ),
                    )
                )
        unique: dict[tuple[str, ...], GraphPath] = {}
        for path in paths:
            unique.setdefault(tuple(path.assertion_ids), path)
        return sorted(
            unique.values(),
            key=lambda item: (item.depth, item.assertion_ids),
        )[:20]

    def _context_entities(
        self,
        context: RequestContext,
        matched: list[KnowledgeEntity],
        edges: list[GraphEdge],
    ) -> list[KnowledgeEntity]:
        result = list(matched)
        known = {item.id for item in result}
        related_ids = sorted(
            {
                entity_id
                for edge in edges
                for entity_id in (edge.subject_id, edge.object_entity_id)
                if entity_id is not None and entity_id not in known
            }
        )
        for entity_id in related_ids:
            try:
                result.append(self._ontology_rag.get_entity(context, entity_id))
            except NotFoundError:
                continue
        return result


def _is_current(edge: GraphEdge, now: datetime) -> bool:
    if edge.valid_from is not None and (
        edge.valid_from.utcoffset() is None or edge.valid_from > now
    ):
        return False
    return not (
        edge.valid_to is not None
        and (edge.valid_to.utcoffset() is None or edge.valid_to <= now)
    )
