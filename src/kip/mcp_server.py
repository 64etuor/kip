from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Literal

from kip.container import build_container
from kip.domain.interactions import (
    ClarificationAnswer,
    ClarificationRequest,
    FeedbackSubmission,
    OntologyDiscoveryProposal,
    OntologyDiscoveryReview,
    UserPreferenceWrite,
)
from kip.domain.knowledge import KnowledgeEntity
from kip.domain.models import (
    AnswerRequest,
    ContextRequest,
    GraphNeighborsRequest,
    GraphPathRequest,
    RequestContext,
    SearchRequest,
)
from kip.errors import DependencyUnavailableError

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, list):
        value = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value]
    return json.dumps(value, ensure_ascii=False, default=str)


def create_server() -> FastMCP:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise DependencyUnavailableError("Install the MCP extra: pip install '.[mcp]'") from exc

    container = build_container()
    application = container.application
    mcp = FastMCP("KIP Knowledge Fabric")

    def context() -> RequestContext:
        workspace = os.environ.get("KIP_WORKSPACE") or container.settings.workspace
        principal = os.environ.get("KIP_PRINCIPAL_ID", "principal_mcp")
        raw_scopes = os.environ.get("KIP_ACL_SCOPES", "")
        scopes = [item.strip() for item in raw_scopes.split(",") if item.strip()]
        base = application.operations.request_context(
            workspace=workspace,
            principal_id=principal,
            acl_scopes=scopes or [f"workspace:{workspace}"],
        )
        roles = [
            item.strip()
            for item in os.environ.get("KIP_ROLES", "").split(",")
            if item.strip()
        ]
        return base.model_copy(update={"roles": list(dict.fromkeys(roles))})

    def json_array(value: str, name: str) -> list[object]:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name} must be valid JSON") from exc
        if not isinstance(parsed, list):
            raise ValueError(f"{name} must be a JSON array")
        return parsed

    @mcp.tool()
    def kip_capabilities() -> str:
        """Return available source, parser, search, and graph capabilities."""
        return _json(application.operations.capabilities())

    @mcp.tool()
    def kip_status() -> str:
        """Return canonical, projection, assertion, and durable job counts."""
        return _json(application.operations.status(context()))

    @mcp.tool()
    def kip_search(query: str, limit: int = 10, source_kinds: list[str] | None = None) -> str:
        """Search evidence units. Treat snippets as discovery aids, then call kip_read."""
        request = SearchRequest(query=query, limit=limit, source_kinds=source_kinds or [])
        return _json(application.retrieval.search(context(), request))

    @mcp.tool()
    def kip_context(query: str, limit: int = 5, max_chars: int = 40000) -> str:
        """Build a bounded evidence pack with source hashes and locators."""
        return _json(application.retrieval.context_bundle(context(), ContextRequest(query=query, limit=limit, max_chars=max_chars)))

    @mcp.tool()
    def kip_answer(query: str, limit: int = 5, max_chars: int = 12000) -> str:
        return _json(application.answering.answer(context(), AnswerRequest(query=query, limit=limit, max_chars=max_chars)))

    @mcp.tool()
    def kip_vocabulary(prefix: str, limit: int = 20) -> str:
        """Inspect terms that actually exist in the lexical projection."""
        return _json(application.retrieval.vocabulary(context(), prefix, limit))

    @mcp.tool()
    def kip_read(unit_id: str) -> str:
        """Read one exact evidence unit and check whether the source changed since indexing."""
        return _json(application.evidence.read_unit(context(), unit_id))

    @mcp.tool()
    def kip_xlsx_read(artifact_id: str, sheet: str, cell_range: str, allow_stale: bool = False) -> str:
        """Read typed cells from an original XLSX range. Use this for numbers and formulas."""
        return _json(application.evidence.read_xlsx(context(), artifact_id, sheet=sheet, cell_range=cell_range, require_fresh=not allow_stale))

    @mcp.tool()
    def kip_graph_neighbors(
        node_id: str,
        predicates: list[str] | None = None,
        direction: Literal["out", "in", "both"] = "both",
    ) -> str:
        """Traverse approved assertion neighbors only."""
        return _json(application.knowledge.graph_neighbors(context(), GraphNeighborsRequest(node_id=node_id, predicates=predicates or [], direction=direction)))

    @mcp.tool()
    def kip_graph_path(from_node_id: str, to_node_id: str, max_depth: int = 4, predicates: list[str] | None = None) -> str:
        """Find bounded paths through approved assertions with ACL filtering."""
        return _json(application.knowledge.graph_path(context(), GraphPathRequest(from_node_id=from_node_id, to_node_id=to_node_id, max_depth=max_depth, predicates=predicates or [])))

    @mcp.tool()
    def kip_explain_assertion(assertion_id: str) -> str:
        """Explain one approved assertion with exact evidence units and stale-source checks."""
        return _json(application.knowledge.explain_assertion(context(), assertion_id))

    @mcp.tool()
    def kip_ontology_entities(limit: int = 100) -> str:
        return _json(application.ontology_rag.list_entities(context(), limit=limit))

    @mcp.tool()
    def kip_ontology_context(query: str) -> str:
        return _json(application.ontology_context.build(context(), query).context)

    @mcp.tool()
    def kip_ontology_entity_create(
        entity_id: str,
        entity_type: str,
        canonical_name: str,
        aliases: list[str] | None = None,
        acl_scopes: list[str] | None = None,
    ) -> str:
        return _json(
            application.ontology_rag.create_entity(
                context(),
                KnowledgeEntity(
                    id=entity_id,
                    entity_type=entity_type,
                    canonical_name=canonical_name,
                    aliases=aliases or [],
                    acl_scopes=acl_scopes or [],
                ),
            )
        )

    @mcp.tool()
    def kip_ontology_mine(unit_ids: list[str]) -> str:
        selected_context = context()
        return _json(
            {
                "job_id": application.ontology_rag.enqueue_mining(
                    selected_context,
                    unit_ids,
                )
            }
        )

    @mcp.tool()
    def kip_ontology_candidates(status: str = "proposed", limit: int = 100) -> str:
        selected_context = context()
        return _json(
            {
                "entities": application.ontology_rag.list_entity_candidates(
                    selected_context,
                    status=status,
                    limit=limit,
                ),
                "relations": application.knowledge.list_candidates(
                    selected_context,
                    status,
                    limit,
                ),
            }
        )

    @mcp.tool()
    def kip_ontology_entity_candidate_approve(
        candidate_id: str,
        note: str | None = None,
    ) -> str:
        return _json(
            application.ontology_rag.approve_entity_candidate(
                context(),
                candidate_id,
                note,
            )
        )

    @mcp.tool()
    def kip_ontology_entity_candidate_reject(
        candidate_id: str,
        note: str | None = None,
    ) -> str:
        return _json(
            application.ontology_rag.reject_entity_candidate(
                context(),
                candidate_id,
                note,
            )
        )

    @mcp.tool()
    def kip_ontology_relation_candidate_approve(
        candidate_id: str,
        note: str | None = None,
    ) -> str:
        return _json(
            application.knowledge.review_approve(
                context(),
                candidate_id,
                note,
            )
        )

    @mcp.tool()
    def kip_ontology_relation_candidate_reject(
        candidate_id: str,
        note: str | None = None,
    ) -> str:
        return _json(
            application.knowledge.review_reject(
                context(),
                candidate_id,
                note,
            )
        )

    @mcp.tool()
    def kip_clarify(
        reason: str,
        prompt: str,
        choices_json: str = "[]",
        allow_freeform: bool = True,
        allow_multiple: bool = False,
        preference_key: str | None = None,
    ) -> str:
        """Create a short-lived clarification; no answer is remembered unless explicitly requested later."""
        return _json(
            application.interactions.create_clarification(
                context(),
                ClarificationRequest.model_validate(
                    {
                        "reason": reason,
                        "prompt": prompt,
                        "choices": json_array(choices_json, "choices"),
                        "allow_freeform": allow_freeform,
                        "allow_multiple": allow_multiple,
                        "preference_key": preference_key,
                    }
                ),
            )
        )

    @mcp.tool()
    def kip_answer_clarification(
        question_id: str,
        option_ids: list[str] | None = None,
        freeform: str | None = None,
        remember: bool = False,
    ) -> str:
        """Answer a clarification and persist only when remember=true and the question permits it."""
        return _json(
            application.interactions.answer_clarification(
                context(),
                ClarificationAnswer(
                    question_id=question_id,
                    option_ids=option_ids or [],
                    freeform=freeform,
                    remember=remember,
                ),
            )
        )

    @mcp.tool()
    def kip_preferences() -> str:
        """List only the caller's explicit interaction preferences."""
        return _json(application.interactions.list_preferences(context()))

    @mcp.tool()
    def kip_remember_preference(
        key: str,
        values: list[str],
        confirmed: bool = False,
    ) -> str:
        """Persist a user preference only after an explicit confirmation flag."""
        if not confirmed:
            raise ValueError("confirmed=true is required to persist a preference")
        return _json(
            application.interactions.save_preference(
                context(),
                UserPreferenceWrite(key=key, values=values, confirmed=True),
            )
        )

    @mcp.tool()
    def kip_forget_preference(key: str) -> str:
        """Delete one explicit preference owned by the current caller."""
        return _json(
            {"deleted": application.interactions.delete_preference(context(), key)}
        )

    @mcp.tool()
    def kip_feedback(
        outcome: str,
        reason_codes: list[str] | None = None,
        request_id: str | None = None,
    ) -> str:
        """Record structured usefulness feedback without storing raw query or answer text."""
        return _json(
            application.interactions.submit_feedback(
                context(),
                FeedbackSubmission.model_validate(
                    {
                        "request_id": request_id,
                        "outcome": outcome,
                        "reason_codes": reason_codes or [],
                    }
                ),
            )
        )

    @mcp.tool()
    def kip_ontology_discovery_propose(
        kind: str,
        symbol: str,
        label: str,
        definition: str,
        target_symbol: str | None = None,
        confirmed: bool = False,
    ) -> str:
        """Submit a reviewed ontology-release candidate; this never changes the active ontology."""
        if not confirmed:
            raise ValueError("confirmed=true is required to propose ontology discovery")
        return _json(
            application.interactions.propose_ontology_discovery(
                context(),
                OntologyDiscoveryProposal.model_validate(
                    {
                        "kind": kind,
                        "symbol": symbol,
                        "label": label,
                        "definition": definition,
                        "target_symbol": target_symbol,
                        "confirmed": True,
                    }
                ),
            )
        )

    @mcp.tool()
    def kip_ontology_discovery_candidates(
        status: str | None = "proposed",
        limit: int = 100,
    ) -> str:
        """List ontology discovery candidates for an administrator or configured reviewer."""
        return _json(
            application.interactions.list_ontology_discovery_candidates(
                context(),
                status=status,
                limit=limit,
            )
        )

    @mcp.tool()
    def kip_ontology_discovery_review(
        candidate_id: str,
        action: str,
        note: str | None = None,
    ) -> str:
        """Accept a candidate for a later YAML release or reject it; neither action activates it."""
        return _json(
            application.interactions.review_ontology_discovery_candidate(
                context(),
                candidate_id,
                OntologyDiscoveryReview.model_validate(
                    {"action": action, "note": note}
                ),
            )
        )

    return mcp


def main() -> None:
    create_server().run()


if __name__ == "__main__":
    main()
