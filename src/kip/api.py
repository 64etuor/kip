from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from kip.container import Container, build_container
from kip.domain.identity import IdentityCredential
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
    ConnectorEvent,
    ContextRequest,
    Envelope,
    EnvelopeMeta,
    ErrorInfo,
    GraphNeighborsRequest,
    GraphPathRequest,
    OntologyMiningSubmission,
    RequestContext,
    SearchRequest,
)
from kip.errors import (
    AuthorizationError,
    KipError,
    ValidationError,
    error_code,
    http_status,
)
from kip.ids import new_id
from kip.settings import Settings


def _serializable_errors(errors: list[Any]) -> list[dict[str, Any]]:
    """Strip values pydantic cannot JSON-encode from validation errors.

    Custom field validators put the raising exception object in `ctx`, and
    `input` can hold arbitrary request data; including either made the error
    response itself fail to serialize.
    """
    cleaned: list[dict[str, Any]] = []
    for error in errors:
        if not isinstance(error, dict):
            cleaned.append({"msg": str(error)})
            continue
        item = {
            key: value
            for key, value in error.items()
            if key not in {"ctx", "input", "url"}
        }
        context = error.get("ctx")
        if isinstance(context, dict):
            item["ctx"] = {key: str(value) for key, value in context.items()}
        cleaned.append(item)
    return cleaned


def create_app(container: Container | None = None) -> FastAPI:
    selected = container or build_container()
    app = FastAPI(
        title="KIP Knowledge Fabric API",
        version="3.1.0",
        description="Optional REST edge adapter over the same application services used by the CLI and MCP.",
    )
    app.state.container = selected

    @app.middleware("http")
    async def request_size_guard(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        length = request.headers.get("content-length")
        if length and int(length) > selected.settings.max_request_bytes:
            return JSONResponse(status_code=413, content={"detail": "request body exceeds configured limit"})
        return await call_next(request)

    def error_envelope(request: Request, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        context = _error_context(selected, request)
        return Envelope(
            ok=False,
            error=ErrorInfo(code=code, message=message, details=details or {}),
            meta=EnvelopeMeta(request_id=context.request_id or new_id("req"), workspace=context.workspace),
        ).model_dump(mode="json")

    @app.exception_handler(HTTPException)
    async def http_error_handler(
        request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        detail: object = exc.detail
        code = "http_error"
        message = str(detail)
        if isinstance(detail, dict):
            code = str(detail.get("code") or code)
            message = str(detail.get("message") or "request failed")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(request, code, message),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_envelope(
                request,
                "request_validation_error",
                "request validation failed",
                {"errors": _serializable_errors(exc.errors())},
            ),
        )

    @app.exception_handler(KipError)
    async def kip_error_handler(request: Request, exc: KipError) -> JSONResponse:
        context = _error_context(selected, request)
        envelope = Envelope(
            ok=False,
            error=ErrorInfo(code=error_code(exc), message=str(exc)),
            meta=EnvelopeMeta(request_id=context.request_id or new_id("req"), workspace=context.workspace),
        )
        return JSONResponse(
            status_code=http_status(exc),
            content=envelope.model_dump(mode="json"),
        )

    async def authenticated_context(
        request: Request,
        x_kip_api_key: str | None = Header(None),
    ) -> RequestContext:
        legacy_headers = (
            "X-KIP-Workspace",
            "X-KIP-Principal",
            "X-KIP-ACL-Scopes",
        )
        if selected.settings.environment not in {"development", "test"} and any(
            request.headers.get(name) is not None for name in legacy_headers
        ):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "untrusted_identity_headers",
                    "message": (
                        "workspace, principal, and ACL scopes must come from the "
                        "configured identity provider"
                    ),
                },
            )
        authorization = request.headers.get("Authorization")
        bearer_token: str | None = None
        if authorization is not None:
            scheme, separator, value = authorization.partition(" ")
            if separator != " " or scheme.lower() != "bearer" or not value.strip():
                raise HTTPException(status_code=401, detail="invalid bearer authorization")
            bearer_token = value.strip()
        scopes_value = request.headers.get("X-KIP-ACL-Scopes")
        asserted_scopes = tuple(
            item.strip() for item in (scopes_value or "").split(",") if item.strip()
        )
        try:
            return selected.identity.resolve(
                IdentityCredential(
                    api_key=x_kip_api_key,
                    bearer_token=bearer_token,
                    asserted_workspace=request.headers.get("X-KIP-Workspace"),
                    asserted_principal_id=request.headers.get("X-KIP-Principal"),
                    asserted_acl_scopes=asserted_scopes,
                ),
                request_id=request.headers.get("X-Request-ID") or new_id("req"),
            )
        except AuthorizationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    async def admin_context(
        request: Request,
        x_kip_api_key: str | None = Header(None),
        x_kip_admin_key: str | None = Header(None),
    ) -> RequestContext:
        context = await authenticated_context(request, x_kip_api_key)
        if selected.settings.identity_mode == "proxy_jwt":
            if "admin" not in context.roles:
                raise HTTPException(status_code=403, detail="admin role is required")
            return context
        expected = selected.settings.admin_key
        if not expected or not hmac.compare_digest(x_kip_admin_key or "", expected):
            raise HTTPException(status_code=403, detail="invalid admin key")
        return context

    def ok(data: Any, context: RequestContext) -> Envelope:
        return Envelope(
            ok=True,
            data=data.model_dump(mode="json") if hasattr(data, "model_dump") else data,
            meta=EnvelopeMeta(request_id=context.request_id or new_id("req"), workspace=context.workspace),
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/capabilities", response_model=Envelope)
    def capabilities(
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(selected.application.operations.capabilities(), context)

    @app.get("/v1/status", response_model=Envelope)
    def status(
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(selected.application.operations.status(context), context)

    @app.post("/v1/search", response_model=Envelope)
    def search(
        payload: SearchRequest,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(selected.application.retrieval.search(context, payload), context)

    @app.post("/v1/context", response_model=Envelope)
    def context_bundle(
        payload: ContextRequest,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(selected.application.retrieval.context_bundle(context, payload), context)

    @app.post("/v1/answer", response_model=Envelope)
    def answer(
        payload: AnswerRequest,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(selected.application.answering.answer(context, payload), context)

    @app.get("/v1/vocabulary", response_model=Envelope)
    def vocabulary(
        prefix: str,
        limit: int = 20,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(selected.application.retrieval.vocabulary(context, prefix, limit), context)

    @app.get("/v1/units/{unit_id}", response_model=Envelope)
    def read_unit(
        unit_id: str,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(selected.application.evidence.read_unit(context, unit_id), context)

    @app.get("/v1/artifacts/{artifact_id}", response_model=Envelope)
    def get_artifact(
        artifact_id: str,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(selected.application.evidence.get_artifact(context, artifact_id), context)

    @app.get("/v1/documents/{document_id}", response_model=Envelope)
    def get_document(
        document_id: str,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(selected.application.evidence.get_document(context, document_id), context)

    @app.get("/v1/assertions/{assertion_id}", response_model=Envelope)
    def get_assertion(
        assertion_id: str,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(selected.application.knowledge.get_assertion(context, assertion_id), context)

    @app.get("/v1/assertions/{assertion_id}/explain", response_model=Envelope)
    def explain_assertion(
        assertion_id: str,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(selected.application.knowledge.explain_assertion(context, assertion_id), context)

    @app.get("/v1/xlsx/{artifact_id}/range", response_model=Envelope)
    def xlsx_range(
        artifact_id: str,
        sheet: str,
        cell_range: str,
        allow_stale: bool = False,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(
            selected.application.evidence.read_xlsx(
                context,
                artifact_id,
                sheet=sheet,
                cell_range=cell_range,
                require_fresh=not allow_stale,
            ),
            context,
        )

    @app.post("/v1/graph/neighbors", response_model=Envelope)
    def graph_neighbors(
        payload: GraphNeighborsRequest,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(selected.application.knowledge.graph_neighbors(context, payload), context)

    @app.post("/v1/graph/path", response_model=Envelope)
    def graph_path(
        payload: GraphPathRequest,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(selected.application.knowledge.graph_path(context, payload), context)

    @app.post("/v1/sync/filesystem/{source_name}", response_model=Envelope)
    def sync_filesystem(
        source_name: str,
        enqueue: bool = True,
        dry_run: bool = False,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        data = (
            {"job_id": selected.application.ingestion.enqueue_sync(context, source_name)}
            if enqueue
            else selected.application.ingestion.sync_filesystem(context, source_name, dry_run=dry_run)
        )
        return ok(data, context)

    @app.post("/v1/sync/{source_name}", response_model=Envelope)
    def enqueue_source_sync(
        source_name: str,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok({"source": source_name, "job_id": selected.application.ingestion.enqueue_sync(context, source_name)}, context)

    @app.get("/v1/jobs", response_model=Envelope)
    def jobs(
        status: str | None = None,
        limit: int = 100,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(selected.application.operations.list_jobs(context, status, limit), context)

    @app.get("/v1/admin/query-traces", response_model=Envelope)
    def query_traces(
        request_id: str | None = None,
        limit: int = 100,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(
            selected.application.telemetry.list_traces(
                context,
                request_id=request_id,
                limit=limit,
            ),
            context,
        )

    @app.delete("/v1/admin/query-traces/expired", response_model=Envelope)
    def prune_query_traces(
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(
            {"deleted": selected.application.telemetry.prune(context)},
            context,
        )

    @app.post("/v1/interactions/clarifications", response_model=Envelope)
    def create_clarification(
        payload: ClarificationRequest,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(
            selected.application.interactions.create_clarification(context, payload),
            context,
        )

    @app.get("/v1/interactions/clarifications/{question_id}", response_model=Envelope)
    def get_clarification(
        question_id: str,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(
            selected.application.interactions.get_clarification(context, question_id),
            context,
        )

    @app.post(
        "/v1/interactions/clarifications/{question_id}/answers",
        response_model=Envelope,
    )
    def answer_clarification(
        question_id: str,
        payload: ClarificationAnswer,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        if payload.question_id != question_id:
            raise ValidationError("clarification answer does not match the question")
        return ok(
            selected.application.interactions.answer_clarification(context, payload),
            context,
        )

    @app.get("/v1/interactions/preferences", response_model=Envelope)
    def list_preferences(
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(selected.application.interactions.list_preferences(context), context)

    @app.put("/v1/interactions/preferences", response_model=Envelope)
    def save_preference(
        payload: UserPreferenceWrite,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(
            selected.application.interactions.save_preference(context, payload),
            context,
        )

    @app.delete("/v1/interactions/preferences/{key}", response_model=Envelope)
    def delete_preference(
        key: str,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(
            {"deleted": selected.application.interactions.delete_preference(context, key)},
            context,
        )

    @app.post("/v1/interactions/feedback", response_model=Envelope)
    def submit_feedback(
        payload: FeedbackSubmission,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(
            selected.application.interactions.submit_feedback(context, payload),
            context,
        )

    @app.delete(
        "/v1/admin/interactions/clarifications/expired",
        response_model=Envelope,
    )
    def prune_expired_clarifications(
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(
            {"deleted": selected.application.interactions.prune_expired_clarifications(context)},
            context,
        )

    @app.post("/v1/connectors/events", response_model=Envelope)
    def connector_event(
        payload: ConnectorEvent,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(selected.application.ingestion.ingest_connector_event(context, payload), context)

    @app.get("/v1/ontology/entities", response_model=Envelope)
    def ontology_entities(
        limit: int = 100,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(
            selected.application.ontology_rag.list_entities(context, limit=limit),
            context,
        )

    @app.post("/v1/ontology/context", response_model=Envelope)
    def ontology_context(
        payload: ContextRequest,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(
            selected.application.ontology_context.build(
                context,
                payload.query,
            ).context,
            context,
        )

    @app.post("/v1/ontology/entities", response_model=Envelope)
    def ontology_entity_create(
        payload: KnowledgeEntity,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(
            selected.application.ontology_rag.create_entity(context, payload),
            context,
        )

    @app.post("/v1/ontology/mining-jobs", response_model=Envelope)
    def ontology_mining_job(
        payload: OntologyMiningSubmission,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(
            {
                "job_id": selected.application.ontology_rag.enqueue_mining(
                    context,
                    payload.unit_ids,
                )
            },
            context,
        )

    @app.post("/v1/ontology/discovery-candidates", response_model=Envelope)
    def propose_ontology_discovery_candidate(
        payload: OntologyDiscoveryProposal,
        context: RequestContext = Depends(authenticated_context),
    ) -> Envelope:
        return ok(
            selected.application.interactions.propose_ontology_discovery(
                context,
                payload,
            ),
            context,
        )

    @app.get("/v1/admin/ontology/discovery-candidates", response_model=Envelope)
    def ontology_discovery_candidates(
        status: str | None = "proposed",
        limit: int = 100,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(
            selected.application.interactions.list_ontology_discovery_candidates(
                context,
                status=status,
                limit=limit,
            ),
            context,
        )

    @app.post(
        "/v1/admin/ontology/discovery-candidates/{candidate_id}/review",
        response_model=Envelope,
    )
    def review_ontology_discovery_candidate(
        candidate_id: str,
        payload: OntologyDiscoveryReview,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(
            selected.application.interactions.review_ontology_discovery_candidate(
                context,
                candidate_id,
                payload,
            ),
            context,
        )

    @app.get("/v1/ontology/entity-candidates", response_model=Envelope)
    def ontology_entity_candidates(
        status: str = "proposed",
        limit: int = 100,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(
            selected.application.ontology_rag.list_entity_candidates(
                context,
                status=status,
                limit=limit,
            ),
            context,
        )

    @app.get(
        "/v1/ontology/entity-candidates/{candidate_id}",
        response_model=Envelope,
    )
    def ontology_entity_candidate(
        candidate_id: str,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(
            selected.application.ontology_rag.get_entity_candidate(
                context,
                candidate_id,
            ),
            context,
        )

    @app.post(
        "/v1/ontology/entity-candidates/{candidate_id}/approve",
        response_model=Envelope,
    )
    def ontology_entity_candidate_approve(
        candidate_id: str,
        note: str | None = None,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(
            selected.application.ontology_rag.approve_entity_candidate(
                context,
                candidate_id,
                note,
            ),
            context,
        )

    @app.post(
        "/v1/ontology/entity-candidates/{candidate_id}/reject",
        response_model=Envelope,
    )
    def ontology_entity_candidate_reject(
        candidate_id: str,
        note: str | None = None,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(
            selected.application.ontology_rag.reject_entity_candidate(
                context,
                candidate_id,
                note,
            ),
            context,
        )

    @app.get("/v1/review/candidates", response_model=Envelope)
    def candidates(
        status: str = "proposed",
        limit: int = 100,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(selected.application.knowledge.list_candidates(context, status, limit), context)

    @app.get("/v1/review/candidates/{candidate_id}", response_model=Envelope)
    def candidate(
        candidate_id: str,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(selected.application.knowledge.get_candidate(context, candidate_id), context)

    @app.post("/v1/review/candidates/{candidate_id}/approve", response_model=Envelope)
    def approve(
        candidate_id: str,
        note: str | None = None,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(selected.application.knowledge.review_approve(context, candidate_id, note), context)

    @app.post("/v1/review/candidates/{candidate_id}/reject", response_model=Envelope)
    def reject(
        candidate_id: str,
        note: str | None = None,
        context: RequestContext = Depends(admin_context),
    ) -> Envelope:
        return ok(selected.application.knowledge.review_reject(context, candidate_id, note), context)

    return app


def _error_context(container: Container, request: Request) -> RequestContext:
    return RequestContext(
        workspace=container.settings.workspace,
        principal_id="unauthenticated",
        acl_scopes=[],
        request_id=request.headers.get("X-Request-ID") or new_id("req"),
    )


def create_app_from_environment() -> FastAPI:
    return create_app(build_container(Settings.load()))


def main() -> None:
    import uvicorn

    settings = Settings.load()
    uvicorn.run(create_app_from_environment(), host=settings.api_host, port=settings.api_port)
