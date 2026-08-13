from __future__ import annotations

from pathlib import Path
from typing import Literal

from kip.application.semantic import SemanticProjectionUseCases
from kip.domain.json_types import JsonObject
from kip.domain.models import (
    Capabilities,
    JobRecord,
    RequestContext,
    StatusReport,
)
from kip.ids import new_id
from kip.ports.embedding import EmbeddingPort
from kip.ports.ingestion import ParserRegistryPort, SourceCatalogPort
from kip.ports.jobs import JobStore
from kip.ports.operations import OperationsStore
from kip.ports.retrieval import RetrievalStore
from kip.settings import Settings


class OperationsUseCases:
    def __init__(
        self,
        settings: Settings,
        store: OperationsStore,
        jobs: JobStore,
        retrieval_store: RetrievalStore,
        sources: SourceCatalogPort,
        parsers: ParserRegistryPort,
        embedding: EmbeddingPort,
    ) -> None:
        self._settings = settings
        self._store = store
        self._jobs = jobs
        self._retrieval_store = retrieval_store
        self._sources = sources
        self._parsers = parsers
        self._embedding = embedding

    def request_context(
        self,
        *,
        workspace: str | None = None,
        principal_id: str = "principal_local",
        acl_scopes: list[str] | None = None,
        roles: list[str] | None = None,
        request_id: str | None = None,
    ) -> RequestContext:
        selected_workspace = workspace or self._settings.workspace
        return RequestContext(
            workspace=selected_workspace,
            principal_id=principal_id,
            acl_scopes=(
                acl_scopes
                if acl_scopes is not None
                else [f"workspace:{selected_workspace}"]
            ),
            roles=list(dict.fromkeys(roles or [])),
            request_id=request_id or new_id("req"),
        )

    def capabilities(
        self,
        context: RequestContext | None = None,
    ) -> Capabilities:
        warnings: list[str] = []
        if self._settings.database_url.startswith("memory://"):
            warnings.append(
                "memory repository is non-durable and intended only for tests or demos"
            )
        semantic_configured = bool(
            self._settings.get("search.semantic_enabled", False)
        )
        selected_context = context or self.request_context()
        projection_status: Literal[
            "disabled",
            "missing",
            "shadow",
            "active",
            "stale",
            "incompatible",
        ] = "disabled"
        semantic_ready = False
        if semantic_configured and self._embedding.name != "disabled":
            verification = SemanticProjectionUseCases(
                self._settings,
                self._retrieval_store,
                self._embedding,
            ).verify(selected_context)
            raw_status = str(verification.get("status", "missing"))
            status_map: dict[
                str,
                Literal["missing", "shadow", "active", "incompatible"],
            ] = {
                "missing": "missing",
                "shadow": "shadow",
                "active": "active",
            }
            projection_status = status_map.get(raw_status, "incompatible")
            if projection_status == "active" and verification.get("ok") is not True:
                projection_status = "stale"
            semantic_ready = (
                projection_status == "active"
                and verification.get("ok") is True
                and verification.get("active") is True
            )
        if semantic_configured and self._embedding.name == "disabled":
            warnings.append(
                "semantic search is enabled but no embedding adapter is configured"
            )
        elif semantic_configured and not semantic_ready:
            warnings.append(
                "semantic search is configured but no compatible complete active space is ready"
            )
        return Capabilities(
            repository=self._store.name,
            lexical_search=True,
            semantic_search=semantic_ready,
            semantic_search_configured=semantic_configured,
            semantic_projection_status=projection_status,
            graph_backend=str(self._settings.get("graph.backend", "postgres")),
            api=True,
            mcp=True,
            parsers=self._parsers.capabilities(),
            connectors=self._sources.capabilities(),
            warnings=warnings,
        )

    def migrate(self) -> list[str]:
        return self._store.migrate(self._settings.project_root / "migrations")

    def status(self, context: RequestContext) -> StatusReport:
        return self._store.status(context)

    def semantic_status(self, context: RequestContext) -> JsonObject:
        return self._retrieval_store.semantic_status(context)

    def rebuild_projection(
        self,
        context: RequestContext,
        projection: str,
    ) -> JsonObject:
        return self._store.rebuild_projection(context, projection)

    def export_canonical(
        self,
        context: RequestContext,
        output: Path,
    ) -> JsonObject:
        return self._store.export_canonical(context, output)

    def list_jobs(
        self,
        context: RequestContext,
        status: str | None = None,
        limit: int = 100,
    ) -> list[JobRecord]:
        return self._jobs.list_jobs(context, status, limit)

    def enqueue_job(
        self,
        context: RequestContext,
        job_type: str,
        payload: JsonObject,
        idempotency_key: str | None = None,
    ) -> str:
        return self._jobs.enqueue_job(
            context,
            job_type,
            payload,
            idempotency_key,
        )

    def claim_job(
        self,
        context: RequestContext,
        worker_id: str,
    ) -> JobRecord | None:
        return self._jobs.claim_job(context, worker_id)

    def complete_job(self, context: RequestContext, job_id: str) -> None:
        self._jobs.complete_job(context, job_id)

    def fail_job(self, context: RequestContext, job_id: str, error: str) -> None:
        self._jobs.fail_job(context, job_id, error)
