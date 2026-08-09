from __future__ import annotations

import json
import os

from kip.container import build_container
from kip.domain.models import (
    AnswerRequest,
    ContextRequest,
    GraphNeighborsRequest,
    GraphPathRequest,
    SearchRequest,
)
from kip.errors import DependencyUnavailableError


def _json(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, list):
        value = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value]
    return json.dumps(value, ensure_ascii=False, default=str)


def create_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise DependencyUnavailableError("Install the MCP extra: pip install '.[mcp]'") from exc

    container = build_container()
    application = container.application
    mcp = FastMCP("KIP Knowledge Fabric")

    def context():
        workspace = os.environ.get("KIP_WORKSPACE") or container.settings.workspace
        principal = os.environ.get("KIP_PRINCIPAL_ID", "principal_mcp")
        raw_scopes = os.environ.get("KIP_ACL_SCOPES", "")
        scopes = [item.strip() for item in raw_scopes.split(",") if item.strip()]
        return application.operations.request_context(
            workspace=workspace,
            principal_id=principal,
            acl_scopes=scopes or [f"workspace:{workspace}"],
        )

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
        return _json(application.retrieval.answer(context(), AnswerRequest(query=query, limit=limit, max_chars=max_chars)))

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
    def kip_graph_neighbors(node_id: str, predicates: list[str] | None = None, direction: str = "both") -> str:
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

    return mcp


def main() -> None:
    create_server().run()


if __name__ == "__main__":
    main()
