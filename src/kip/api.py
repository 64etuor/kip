from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from kip.container import Container, build_container
from kip.domain.models import (
    AnswerRequest,
    ConnectorEvent,
    ContextRequest,
    Envelope,
    EnvelopeMeta,
    ErrorInfo,
    GraphNeighborsRequest,
    GraphPathRequest,
    RequestContext,
    SearchRequest,
)
from kip.errors import AuthorizationError, ConflictError, KipError, NotFoundError, ValidationError
from kip.ids import new_id
from kip.settings import Settings


def create_app(container: Container | None = None) -> FastAPI:
    selected = container or build_container()
    app = FastAPI(
        title="KIP Knowledge Fabric API",
        version="3.1.0",
        description="Optional REST edge adapter over the same application services used by the CLI and MCP.",
    )
    app.state.container = selected

    @app.middleware("http")
    async def request_size_guard(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and int(length) > selected.settings.max_request_bytes:
            return JSONResponse(status_code=413, content={"detail": "request body exceeds configured limit"})
        return await call_next(request)

    def error_envelope(request: Request, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        context = _context_from_headers(selected, request)
        return Envelope(
            ok=False,
            error=ErrorInfo(code=code, message=message, details=details or {}),
            meta=EnvelopeMeta(request_id=context.request_id or new_id("req"), workspace=context.workspace),
        ).model_dump(mode="json")

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(request, "http_error", str(exc.detail)),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=error_envelope(request, "request_validation_error", "request validation failed", {"errors": exc.errors()}),
        )

    @app.exception_handler(KipError)
    async def kip_error_handler(request: Request, exc: KipError):
        status = 500
        code = "internal_error"
        if isinstance(exc, NotFoundError):
            status, code = 404, "not_found"
        elif isinstance(exc, ValidationError):
            status, code = 422, "validation_error"
        elif isinstance(exc, ConflictError):
            status, code = 409, "conflict"
        elif isinstance(exc, AuthorizationError):
            status, code = 403, "forbidden"
        context = _context_from_headers(selected, request)
        envelope = Envelope(
            ok=False,
            error=ErrorInfo(code=code, message=str(exc)),
            meta=EnvelopeMeta(request_id=context.request_id or new_id("req"), workspace=context.workspace),
        )
        return JSONResponse(status_code=status, content=envelope.model_dump(mode="json"))

    async def authenticated_context(
        request: Request,
        x_kip_api_key: str | None = Header(None),
    ) -> RequestContext:
        expected = selected.settings.api_key
        require = selected.settings.environment not in {"development", "test"} or bool(expected)
        if require and (not expected or x_kip_api_key != expected):
            raise HTTPException(status_code=401, detail="invalid API key")
        return _context_from_headers(selected, request)

    async def admin_context(
        request: Request,
        x_kip_api_key: str | None = Header(None),
        x_kip_admin_key: str | None = Header(None),
    ) -> RequestContext:
        context = await authenticated_context(request, x_kip_api_key)
        expected = selected.settings.admin_key
        if not expected or x_kip_admin_key != expected:
            raise HTTPException(status_code=403, detail="invalid admin key")
        return context

    def ok(data: Any, context: RequestContext) -> Envelope:
        return Envelope(
            ok=True,
            data=data.model_dump(mode="json") if hasattr(data, "model_dump") else data,
            meta=EnvelopeMeta(request_id=context.request_id or new_id("req"), workspace=context.workspace),
        )

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/v1/capabilities", response_model=Envelope)
    async def capabilities(context: RequestContext = Depends(authenticated_context)):
        return ok(selected.service.capabilities(), context)

    @app.get("/v1/status", response_model=Envelope)
    async def status(context: RequestContext = Depends(authenticated_context)):
        return ok(selected.repository.status(context), context)

    @app.post("/v1/search", response_model=Envelope)
    async def search(payload: SearchRequest, context: RequestContext = Depends(authenticated_context)):
        return ok(selected.service.search(context, payload), context)

    @app.post("/v1/context", response_model=Envelope)
    async def context_bundle(payload: ContextRequest, context: RequestContext = Depends(authenticated_context)):
        return ok(selected.service.context_bundle(context, payload), context)

    @app.post("/v1/answer", response_model=Envelope)
    async def answer(payload: AnswerRequest, context: RequestContext = Depends(authenticated_context)):
        return ok(selected.service.answer(context, payload), context)

    @app.get("/v1/vocabulary", response_model=Envelope)
    async def vocabulary(prefix: str, limit: int = 20, context: RequestContext = Depends(authenticated_context)):
        return ok(selected.service.vocabulary(context, prefix, limit), context)

    @app.get("/v1/units/{unit_id}", response_model=Envelope)
    async def read_unit(unit_id: str, context: RequestContext = Depends(authenticated_context)):
        return ok(selected.service.read_unit(context, unit_id), context)

    @app.get("/v1/artifacts/{artifact_id}", response_model=Envelope)
    async def get_artifact(artifact_id: str, context: RequestContext = Depends(authenticated_context)):
        return ok(selected.repository.get_artifact(context, artifact_id), context)

    @app.get("/v1/documents/{document_id}", response_model=Envelope)
    async def get_document(document_id: str, context: RequestContext = Depends(authenticated_context)):
        return ok(selected.repository.get_document(context, document_id), context)

    @app.get("/v1/assertions/{assertion_id}", response_model=Envelope)
    async def get_assertion(assertion_id: str, context: RequestContext = Depends(authenticated_context)):
        return ok(selected.repository.get_assertion(context, assertion_id), context)

    @app.get("/v1/assertions/{assertion_id}/explain", response_model=Envelope)
    async def explain_assertion(assertion_id: str, context: RequestContext = Depends(authenticated_context)):
        return ok(selected.service.explain_assertion(context, assertion_id), context)

    @app.get("/v1/xlsx/{artifact_id}/range", response_model=Envelope)
    async def xlsx_range(
        artifact_id: str,
        sheet: str,
        cell_range: str,
        allow_stale: bool = False,
        context: RequestContext = Depends(authenticated_context),
    ):
        return ok(
            selected.service.read_xlsx(
                context,
                artifact_id,
                sheet=sheet,
                cell_range=cell_range,
                require_fresh=not allow_stale,
            ),
            context,
        )

    @app.post("/v1/graph/neighbors", response_model=Envelope)
    async def graph_neighbors(payload: GraphNeighborsRequest, context: RequestContext = Depends(authenticated_context)):
        return ok(selected.repository.graph_neighbors(context, payload), context)

    @app.post("/v1/graph/path", response_model=Envelope)
    async def graph_path(payload: GraphPathRequest, context: RequestContext = Depends(authenticated_context)):
        return ok(selected.repository.graph_path(context, payload), context)

    @app.post("/v1/sync/filesystem/{source_name}", response_model=Envelope)
    async def sync_filesystem(
        source_name: str,
        enqueue: bool = True,
        dry_run: bool = False,
        context: RequestContext = Depends(admin_context),
    ):
        data = (
            {"job_id": selected.service.enqueue_sync(context, source_name)}
            if enqueue
            else selected.service.sync_filesystem(context, source_name, dry_run=dry_run)
        )
        return ok(data, context)

    @app.post("/v1/sync/{source_name}", response_model=Envelope)
    async def enqueue_source_sync(
        source_name: str,
        context: RequestContext = Depends(admin_context),
    ):
        return ok({"source": source_name, "job_id": selected.service.enqueue_sync(context, source_name)}, context)

    @app.get("/v1/jobs", response_model=Envelope)
    async def jobs(
        status: str | None = None,
        limit: int = 100,
        context: RequestContext = Depends(admin_context),
    ):
        return ok(selected.repository.list_jobs(context, status, limit), context)

    @app.post("/v1/connectors/events", response_model=Envelope)
    async def connector_event(payload: ConnectorEvent, context: RequestContext = Depends(admin_context)):
        return ok(selected.service.ingest_connector_event(context, payload), context)

    @app.get("/v1/review/candidates", response_model=Envelope)
    async def candidates(
        status: str = "proposed",
        limit: int = 100,
        context: RequestContext = Depends(admin_context),
    ):
        return ok(selected.repository.list_candidates(context, status, limit), context)

    @app.get("/v1/review/candidates/{candidate_id}", response_model=Envelope)
    async def candidate(candidate_id: str, context: RequestContext = Depends(admin_context)):
        return ok(selected.repository.get_candidate(context, candidate_id), context)

    @app.post("/v1/review/candidates/{candidate_id}/approve", response_model=Envelope)
    async def approve(candidate_id: str, note: str | None = None, context: RequestContext = Depends(admin_context)):
        return ok(selected.service.review_approve(context, candidate_id, note), context)

    @app.post("/v1/review/candidates/{candidate_id}/reject", response_model=Envelope)
    async def reject(candidate_id: str, note: str | None = None, context: RequestContext = Depends(admin_context)):
        return ok(selected.service.review_reject(context, candidate_id, note), context)

    return app


def _context_from_headers(container: Container, request: Request) -> RequestContext:
    workspace = request.headers.get("X-KIP-Workspace") or container.settings.workspace
    principal = request.headers.get("X-KIP-Principal") or "principal_api"
    scopes_value = request.headers.get("X-KIP-ACL-Scopes")
    scopes = [item.strip() for item in scopes_value.split(",") if item.strip()] if scopes_value else [f"workspace:{workspace}"]
    return container.service.request_context(
        workspace=workspace,
        principal_id=principal,
        acl_scopes=scopes,
        request_id=request.headers.get("X-Request-ID") or new_id("req"),
    )


def create_app_from_environment() -> FastAPI:
    return create_app(build_container(Settings.load()))


def main() -> None:
    import uvicorn

    settings = Settings.load()
    uvicorn.run(create_app_from_environment(), host=settings.api_host, port=settings.api_port)
