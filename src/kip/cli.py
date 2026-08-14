from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from kip.container import Container, build_container
from kip.domain.interactions import (
    ClarificationAnswer,
    ClarificationRequest,
    FeedbackSubmission,
    OntologyDiscoveryProposal,
    OntologyDiscoveryReview,
    UserPreferenceWrite,
)
from kip.domain.knowledge import CandidateEvidence, KnowledgeEntity
from kip.domain.models import (
    AnswerRequest,
    AssertionCandidate,
    ContextRequest,
    Envelope,
    EnvelopeMeta,
    ErrorInfo,
    GraphNeighborsRequest,
    GraphPathRequest,
    RequestContext,
    SearchHit,
    SearchMode,
    SearchRequest,
)
from kip.errors import KipError, NotFoundError, ValidationError, error_code
from kip.evaluation.models import GoldenCase
from kip.evaluation.reporting import append_evolution_record, write_report
from kip.evaluation.reviews import load_review_bundle
from kip.evaluation.runner import (
    compare_variants,
    load_dataset,
    requires_stale_enrichment,
    run_evaluation,
    validate_activation_report,
)
from kip.ids import new_id
from kip.ontology import OntologyCatalog, validate_ontology
from kip.ontology_migration import (
    diff_ontologies,
    load_migration,
    validate_migration_coverage,
)
from kip.quality import load_experiment, load_quality_report, recommend
from kip.settings import Settings
from kip.setup_cli import setup_app

app = typer.Typer(no_args_is_help=True, add_completion=False, help="KIP knowledge fabric CLI")
sync_app = typer.Typer(no_args_is_help=True, help="Synchronize configured sources")
xlsx_app = typer.Typer(no_args_is_help=True, help="Read exact XLSX ranges")
graph_app = typer.Typer(no_args_is_help=True, help="Traverse approved assertions")
review_app = typer.Typer(no_args_is_help=True, help="Review assertion candidates")
jobs_app = typer.Typer(no_args_is_help=True, help="Inspect durable jobs")
api_app = typer.Typer(no_args_is_help=True, help="Run the optional REST adapter")
worker_app = typer.Typer(no_args_is_help=True, help="Run background jobs")
get_app = typer.Typer(no_args_is_help=True, help="Get canonical objects")
projection_app = typer.Typer(
    no_args_is_help=True, help="Inspect and rebuild disposable projections"
)
export_app = typer.Typer(no_args_is_help=True, help="Export portable canonical bundles")
evaluate_app = typer.Typer(no_args_is_help=True, help="Measure retrieval quality")
quality_app = typer.Typer(no_args_is_help=True, help="Evaluate version-pinned candidates")
ontology_app = typer.Typer(no_args_is_help=True, help="Validate and migrate ontology releases")
telemetry_app = typer.Typer(no_args_is_help=True, help="Inspect redacted RAG query traces")
parser_app = typer.Typer(no_args_is_help=True, help="Shadow and activate parser candidates")
interaction_app = typer.Typer(
    no_args_is_help=True,
    help="Ask bounded clarification questions and manage opt-in interaction memory",
)
ontology_discovery_app = typer.Typer(
    no_args_is_help=True,
    help="Propose and review non-activating ontology discovery candidates",
)

app.add_typer(sync_app, name="sync")
app.add_typer(xlsx_app, name="xlsx")
app.add_typer(graph_app, name="graph")
app.add_typer(review_app, name="review")
app.add_typer(jobs_app, name="jobs")
app.add_typer(api_app, name="api")
app.add_typer(worker_app, name="worker")
app.add_typer(get_app, name="get")
app.add_typer(projection_app, name="projection")
app.add_typer(export_app, name="export")
app.add_typer(evaluate_app, name="evaluate")
app.add_typer(quality_app, name="quality")
app.add_typer(ontology_app, name="ontology")
app.add_typer(telemetry_app, name="telemetry")
app.add_typer(parser_app, name="parser")
app.add_typer(interaction_app, name="interaction")
app.add_typer(setup_app, name="setup")
ontology_app.add_typer(ontology_discovery_app, name="discovery")


class Runtime:
    def __init__(self, container: Container, context: RequestContext) -> None:
        self.container = container
        self.context = context


def command_loads_models(subcommand: str | None) -> bool:
    return subcommand not in {
        "migrate",
        "parser",
        "quality",
        "telemetry",
        "interaction",
    }


def _is_command_line_parameter(ctx: typer.Context, name: str) -> bool:
    source = ctx.get_parameter_source(name)
    return source is not None and source.name == "COMMANDLINE"


@app.callback()
def root(
    ctx: typer.Context,
    config: Path | None = typer.Option(
        None, "--config", envvar="KIP_CONFIG", help="TOML configuration path"
    ),
    workspace: str | None = typer.Option(None, "--workspace", envvar="KIP_WORKSPACE"),
    principal: str = typer.Option("principal_local", "--principal", envvar="KIP_PRINCIPAL_ID"),
    acl_scope: list[str] | None = typer.Option(None, "--acl-scope", help="Repeatable access scope"),
    acl_scopes: str | None = typer.Option(
        None,
        "--acl-scopes",
        envvar="KIP_ACL_SCOPES",
        help="Comma-separated access scopes",
    ),
    role: list[str] | None = typer.Option(
        None,
        "--role",
        help="Repeatable operator role (admin commands fail without an explicit role)",
    ),
    roles: str | None = typer.Option(
        None,
        "--roles",
        envvar="KIP_ROLES",
        help="Comma-separated operator roles",
    ),
) -> None:
    if ctx.invoked_subcommand == "setup":
        ctx.obj = None
        return
    settings = Settings.load(config)
    container = build_container(
        settings,
        load_models=command_loads_models(ctx.invoked_subcommand),
    )
    explicit_repeated_scopes = _is_command_line_parameter(ctx, "acl_scope")
    explicit_csv_scopes = _is_command_line_parameter(ctx, "acl_scopes")
    selected_scopes = list(acl_scope or [])
    if not explicit_repeated_scopes and acl_scopes:
        selected_scopes.extend(item.strip() for item in acl_scopes.split(",") if item.strip())
    selected_roles = list(role or [])
    if roles:
        selected_roles.extend(item.strip() for item in roles.split(",") if item.strip())
    request_context = container.application.operations.request_context(
        workspace=workspace,
        principal_id=principal,
        acl_scopes=(
            list(dict.fromkeys(selected_scopes))
            if selected_scopes or explicit_repeated_scopes or explicit_csv_scopes
            else None
        ),
        roles=selected_roles or None,
    )
    ctx.obj = Runtime(container, request_context)


def _runtime(ctx: typer.Context) -> Runtime:
    value = ctx.find_root().obj
    if not isinstance(value, Runtime):
        raise RuntimeError("CLI runtime is unavailable")
    return value


def _emit(runtime: Runtime, data: Any) -> None:
    envelope = Envelope(
        ok=True,
        data=data.model_dump(mode="json") if hasattr(data, "model_dump") else data,
        meta=EnvelopeMeta(
            request_id=runtime.context.request_id or new_id("req"),
            workspace=runtime.context.workspace,
        ),
    )
    typer.echo(envelope.model_dump_json(indent=2))


def _emit_error(runtime: Runtime | None, exc: BaseException) -> None:
    context = runtime.context if runtime else RequestContext(request_id=new_id("req"))
    envelope = Envelope(
        ok=False,
        error=ErrorInfo(code=error_code(exc), message=str(exc)),
        meta=EnvelopeMeta(
            request_id=context.request_id or new_id("req"),
            workspace=context.workspace,
        ),
    )
    typer.echo(envelope.model_dump_json(indent=2), err=True)


def _run(ctx: typer.Context, function: Callable[[Runtime], Any]) -> None:
    runtime: Runtime | None = None
    try:
        runtime = _runtime(ctx)
        _emit(runtime, function(runtime))
    except KipError as exc:
        _emit_error(runtime, exc)
        raise typer.Exit(code=4 if isinstance(exc, NotFoundError) else 3) from exc
    except KeyboardInterrupt as exc:
        if runtime:
            _emit_error(runtime, exc)
        raise typer.Exit(code=130) from exc
    except Exception as exc:
        _emit_error(runtime, exc)
        raise typer.Exit(code=1) from exc


def _split_values(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        result.extend(item.strip() for item in value.split(",") if item.strip())
    return list(dict.fromkeys(result))


def _split_csv(value: str) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


def _validated_search_mode(value: str | None) -> SearchMode | None:
    match value:
        case None | "lexical" | "vector" | "hybrid" | "reranked":
            return value
        case _:
            raise ValidationError(f"unsupported search mode: {value}")


def _resolve_ontology_version(runtime: Runtime, explicit: str | None) -> str:
    """Resolve `--ontology-version`, defaulting to the loaded catalog's version.

    `validate_candidate` enforces strict equality with the active catalog
    version, so a stale hardcoded default goes wrong the moment the ontology
    is released past its initial version. Each CLI invocation is a fresh
    process, so this always reflects the on-disk ontology files.
    """
    if explicit is not None:
        return explicit
    ontology = runtime.container.ontology
    if ontology is None:
        raise ValidationError(
            "--ontology-version is required when no ontology contract is loaded"
        )
    return ontology.version


def _enabled_filesystem_sources(runtime: Runtime) -> list[str]:
    names: list[str] = []
    for source in runtime.container.settings.get("sources.filesystem", []) or []:
        if isinstance(source, dict) and source.get("enabled", True) and source.get("name"):
            names.append(str(source["name"]))
    return names


def _resolve_sync_source(runtime: Runtime, source: str) -> str:
    normalized = source.strip().lower()
    filesystem_sources = _enabled_filesystem_sources(runtime)
    if source in filesystem_sources:
        return source
    if normalized in {"nas", "filesystem", "files"}:
        if len(filesystem_sources) == 1:
            return filesystem_sources[0]
        if not filesystem_sources:
            raise ValidationError("no enabled filesystem source is configured")
        raise ValidationError(
            "multiple filesystem sources are configured; use one of: "
            + ", ".join(filesystem_sources)
        )
    if normalized == "mail":
        candidates = [
            name
            for name, enabled in [
                ("apple-mail", runtime.container.settings.get("sources.apple_mail.enabled", False)),
                ("imap", runtime.container.settings.get("sources.imap.enabled", False)),
            ]
            if enabled
        ]
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise ValidationError("neither Apple Mail nor IMAP is enabled")
        raise ValidationError("both Apple Mail and IMAP are enabled; choose apple-mail or imap")
    if normalized in {"slack", "apple-mail", "imap", "all"}:
        return normalized
    raise ValidationError(f"unknown source: {source}")


def _sync_one(
    runtime: Runtime,
    source: str,
    *,
    enqueue: bool = False,
    dry_run: bool = False,
    since: str | None = None,
) -> Any:
    selected = _resolve_sync_source(runtime, source)
    if selected == "all":
        results: list[Any] = []
        for name in _enabled_filesystem_sources(runtime):
            results.append(_sync_one(runtime, name, enqueue=enqueue, dry_run=dry_run, since=since))
        for name, enabled in [
            ("slack", runtime.container.settings.get("sources.slack.enabled", False)),
            ("apple-mail", runtime.container.settings.get("sources.apple_mail.enabled", False)),
            ("imap", runtime.container.settings.get("sources.imap.enabled", False)),
        ]:
            if enabled:
                results.append(
                    _sync_one(runtime, name, enqueue=enqueue, dry_run=dry_run, since=since)
                )
        return results
    if enqueue:
        if dry_run:
            raise ValidationError("--enqueue and --dry-run cannot be combined")
        return {
            "source": selected,
            "job_id": runtime.container.application.ingestion.enqueue_sync(
                runtime.context, selected
            ),
        }
    if selected in _enabled_filesystem_sources(runtime):
        return runtime.container.application.ingestion.sync_filesystem(
            runtime.context, selected, dry_run=dry_run
        )
    if dry_run:
        raise ValidationError("--dry-run is currently supported only for filesystem sources")
    if selected == "slack":
        return runtime.container.application.ingestion.sync_slack(runtime.context, oldest=since)
    if selected == "apple-mail":
        return runtime.container.application.ingestion.sync_apple_mail(runtime.context)
    if selected == "imap":
        return runtime.container.application.ingestion.sync_imap(runtime.context)
    raise ValidationError(f"unsupported source: {selected}")


@app.command()
def capabilities(ctx: typer.Context) -> None:
    """Report available parsers, connectors, projections, and edge adapters."""
    _run(
        ctx,
        lambda runtime: runtime.container.application.operations.capabilities(runtime.context),
    )


@app.command()
def status(ctx: typer.Context) -> None:
    """Report canonical and projection counts."""
    _run(
        ctx,
        lambda runtime: runtime.container.application.operations.status(runtime.context),
    )


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Check configuration, source mounts, storage, and adapter availability."""

    def action(runtime: Runtime) -> Any:
        settings = runtime.container.settings
        capabilities = runtime.container.application.operations.capabilities(runtime.context)
        checks: list[dict[str, Any]] = []
        checks.append(
            {
                "name": "configuration",
                "ok": settings.config_path.exists(),
                "required": settings.environment not in {"development", "test"},
                "details": {"path": str(settings.config_path)},
            }
        )
        checks.append(
            {
                "name": "canonical_repository",
                "ok": capabilities.repository in {"postgresql", "memory"},
                "required": True,
                "details": {"backend": capabilities.repository},
            }
        )
        checks.append(
            {
                "name": "content_addressed_store",
                "ok": settings.cas_path.exists() and settings.cas_path.is_dir(),
                "required": True,
                "details": {"path": str(settings.cas_path)},
            }
        )
        for source in settings.get("sources.filesystem", []) or []:
            if not isinstance(source, dict) or not source.get("enabled", True):
                continue
            root_value = source.get("root")
            root_path = Path(str(root_value)).expanduser().resolve() if root_value else None
            checks.append(
                {
                    "name": f"filesystem_source:{source.get('name', 'unnamed')}",
                    "ok": bool(root_path and root_path.exists() and root_path.is_dir()),
                    "required": True,
                    "details": {
                        "path": str(root_path) if root_path else None,
                        "configured_read_only": bool(source.get("read_only", False)),
                    },
                }
            )
        required_failures = [item["name"] for item in checks if item["required"] and not item["ok"]]
        return {
            "healthy": not required_failures,
            "required_failures": required_failures,
            "checks": checks,
            "capabilities": capabilities.model_dump(mode="json"),
        }

    _run(ctx, action)


@app.command()
def migrate(ctx: typer.Context) -> None:
    """Apply append-only PostgreSQL migrations."""
    _run(ctx, lambda runtime: {"applied": runtime.container.application.operations.migrate()})


@app.command()
def search(
    ctx: typer.Context,
    query: str | None = typer.Argument(None),
    query_option: str | None = typer.Option(None, "--query"),
    limit: int = typer.Option(10, min=1, max=100),
    mode: str | None = typer.Option(None, "--mode"),
    source_kind: list[str] | None = typer.Option(None, "--source-kind"),
    document_type: list[str] | None = typer.Option(None, "--document-type"),
    project_id: list[str] | None = typer.Option(None, "--project-id"),
    include_candidate_assertions: bool = typer.Option(
        False,
        "--include-candidate-assertions",
    ),
) -> None:
    """Search exact identifiers and lexical evidence units."""

    def action(runtime: Runtime) -> Any:
        selected_query = query_option or query
        if not selected_query:
            raise ValidationError("provide QUERY or --query")
        request = SearchRequest(
            query=selected_query,
            limit=limit,
            mode=_validated_search_mode(mode),
            source_kinds=_split_values(source_kind),
            document_types=_split_values(document_type),
            project_ids=_split_values(project_id),
            include_candidate_assertions=include_candidate_assertions,
        )
        return runtime.container.application.retrieval.search(runtime.context, request)

    _run(ctx, action)


@app.command()
def vocab(
    ctx: typer.Context,
    prefix: str | None = typer.Argument(None),
    term: str | None = typer.Option(None, "--term"),
    limit: int = typer.Option(20, min=1, max=100),
) -> None:
    """Inspect terms that actually exist in the lexical projection."""

    def action(runtime: Runtime) -> Any:
        selected = term or prefix
        if not selected:
            raise ValidationError("provide PREFIX or --term")
        return runtime.container.application.retrieval.vocabulary(runtime.context, selected, limit)

    _run(ctx, action)


@app.command(name="context")
def context_command(
    ctx: typer.Context,
    query: str | None = typer.Argument(None),
    query_option: str | None = typer.Option(None, "--query"),
    limit: int = typer.Option(5, min=1, max=30),
    max_chars: int = typer.Option(40000, min=1000, max=200000),
    mode: str | None = typer.Option(None, "--mode"),
    source_kind: list[str] | None = typer.Option(None, "--source-kind"),
    document_type: list[str] | None = typer.Option(None, "--document-type"),
    project_id: list[str] | None = typer.Option(None, "--project-id"),
    include_candidate_assertions: bool = typer.Option(
        False,
        "--include-candidate-assertions",
    ),
) -> None:
    """Build a bounded evidence bundle for an AI agent or application."""

    def action(runtime: Runtime) -> Any:
        selected_query = query_option or query
        if not selected_query:
            raise ValidationError("provide QUERY or --query")
        request = ContextRequest(
            query=selected_query,
            limit=limit,
            max_chars=max_chars,
            mode=_validated_search_mode(mode),
            source_kinds=_split_values(source_kind),
            document_types=_split_values(document_type),
            project_ids=_split_values(project_id),
            include_candidate_assertions=include_candidate_assertions,
        )
        return runtime.container.application.retrieval.context_bundle(runtime.context, request)

    _run(ctx, action)


@app.command()
def answer(
    ctx: typer.Context,
    query: str | None = typer.Argument(None),
    query_option: str | None = typer.Option(None, "--query"),
    limit: int = typer.Option(5, min=1, max=20),
    max_chars: int = typer.Option(12000, min=1000, max=40000),
    mode: str | None = typer.Option(None, "--mode"),
    source_kind: list[str] | None = typer.Option(None, "--source-kind"),
    document_type: list[str] | None = typer.Option(None, "--document-type"),
    project_id: list[str] | None = typer.Option(None, "--project-id"),
    include_candidate_assertions: bool = typer.Option(
        False,
        "--include-candidate-assertions",
    ),
) -> None:
    def action(runtime: Runtime) -> Any:
        selected_query = query_option or query
        if not selected_query:
            raise ValidationError("provide QUERY or --query")
        return runtime.container.application.answering.answer(
            runtime.context,
            AnswerRequest(
                query=selected_query,
                limit=limit,
                max_chars=max_chars,
                mode=_validated_search_mode(mode),
                source_kinds=_split_values(source_kind),
                document_types=_split_values(document_type),
                project_ids=_split_values(project_id),
                include_candidate_assertions=include_candidate_assertions,
            ),
        )

    _run(ctx, action)


@app.command()
def read(
    ctx: typer.Context,
    unit_id: str | None = typer.Argument(None),
    unit_id_option: str | None = typer.Option(None, "--unit-id"),
) -> None:
    """Read one exact evidence unit and report source staleness."""

    def action(runtime: Runtime) -> Any:
        selected = unit_id_option or unit_id
        if not selected:
            raise ValidationError("provide UNIT_ID or --unit-id")
        return runtime.container.application.evidence.read_unit(
            runtime.context,
            selected,
        )

    _run(ctx, action)


@get_app.command("artifact")
def get_artifact(ctx: typer.Context, artifact_id: str = typer.Argument(...)) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.evidence.get_artifact(
            runtime.context,
            artifact_id,
        ),
    )


@get_app.command("document")
def get_document(ctx: typer.Context, document_id: str = typer.Argument(...)) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.evidence.get_document(
            runtime.context,
            document_id,
        ),
    )


@get_app.command("candidate")
def get_candidate(ctx: typer.Context, candidate_id: str = typer.Argument(...)) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.knowledge.get_candidate(
            runtime.context,
            candidate_id,
        ),
    )


@get_app.command("assertion")
def get_assertion(ctx: typer.Context, assertion_id: str = typer.Argument(...)) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.knowledge.get_assertion(
            runtime.context,
            assertion_id,
        ),
    )


@app.command()
def explain(ctx: typer.Context, assertion_id: str = typer.Option(..., "--assertion-id")) -> None:
    """Explain an approved assertion with its exact evidence units."""
    _run(
        ctx,
        lambda runtime: runtime.container.application.knowledge.explain_assertion(
            runtime.context, assertion_id
        ),
    )


@sync_app.command("all")
def sync_all(
    ctx: typer.Context,
    enqueue: bool = typer.Option(False, "--enqueue"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    _run(ctx, lambda runtime: _sync_one(runtime, "all", enqueue=enqueue, dry_run=dry_run))


@sync_app.command("run")
def sync_run(
    ctx: typer.Context,
    source: str = typer.Option(
        ..., "--source", help="Configured source name, nas, slack, mail, or all"
    ),
    mode: str = typer.Option("incremental", "--mode", help="Starter supports incremental mode"),
    since: str | None = typer.Option(None, "--since", help="Optional Slack oldest timestamp"),
    enqueue: bool = typer.Option(False, "--enqueue"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Compatibility entry point for source-neutral synchronization."""

    def action(runtime: Runtime) -> Any:
        if mode != "incremental":
            raise ValidationError(
                "the starter kit implements safe incremental sync only; "
                "source reconciliation and forced full re-extraction require an explicit operator workflow"
            )
        return _sync_one(runtime, source, enqueue=enqueue, dry_run=dry_run, since=since)

    _run(ctx, action)


@sync_app.command("filesystem")
def sync_filesystem(
    ctx: typer.Context,
    source: str = typer.Option(..., "--source"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    enqueue: bool = typer.Option(
        False, "--enqueue", help="Create a durable worker job instead of running now"
    ),
) -> None:
    _run(ctx, lambda runtime: _sync_one(runtime, source, enqueue=enqueue, dry_run=dry_run))


@sync_app.command("slack")
def sync_slack(ctx: typer.Context, oldest: str | None = typer.Option(None)) -> None:
    _run(ctx, lambda runtime: _sync_one(runtime, "slack", since=oldest))


@sync_app.command("imap")
def sync_imap(ctx: typer.Context) -> None:
    _run(ctx, lambda runtime: _sync_one(runtime, "imap"))


@sync_app.command("apple-mail")
def sync_apple_mail(ctx: typer.Context) -> None:
    _run(ctx, lambda runtime: _sync_one(runtime, "apple-mail"))


@parser_app.command("reextract")
def parser_reextract(
    ctx: typer.Context,
    source: str = typer.Option(..., "--source", help="Configured filesystem source or nas"),
    activate: bool = typer.Option(
        False,
        "--activate",
        help="Atomically replace active units after all safety gates pass",
    ),
) -> None:
    def action(runtime: Runtime) -> Any:
        selected = _resolve_sync_source(runtime, source)
        if selected not in _enabled_filesystem_sources(runtime):
            raise ValidationError("parser re-extraction requires one filesystem source")
        return runtime.container.application.ingestion.reextract_filesystem(
            runtime.context,
            selected,
            activate=activate,
        )

    _run(ctx, action)


@xlsx_app.command("read")
def xlsx_read(
    ctx: typer.Context,
    artifact_id: str = typer.Argument(...),
    sheet: str = typer.Option(..., "--sheet"),
    cell_range: str = typer.Option(..., "--range"),
    allow_stale: bool = typer.Option(False, "--allow-stale"),
) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.evidence.read_xlsx(
            runtime.context,
            artifact_id,
            sheet=sheet,
            cell_range=cell_range,
            require_fresh=not allow_stale,
        ),
    )


@app.command(name="xlsx-read")
def xlsx_read_alias(
    ctx: typer.Context,
    artifact_id: str | None = typer.Argument(None),
    artifact_id_option: str | None = typer.Option(None, "--artifact-id"),
    sheet: str = typer.Option(..., "--sheet"),
    cell_range: str = typer.Option(..., "--range"),
    require_fresh: bool = typer.Option(True, "--require-fresh/--allow-stale"),
) -> None:
    """Read an exact XLSX range; stable top-level alias for agents and apps."""

    def action(runtime: Runtime) -> Any:
        selected_artifact = artifact_id_option or artifact_id
        if not selected_artifact:
            raise ValidationError("provide ARTIFACT_ID or --artifact-id")
        return runtime.container.application.evidence.read_xlsx(
            runtime.context,
            selected_artifact,
            sheet=sheet,
            cell_range=cell_range,
            require_fresh=require_fresh,
        )

    _run(ctx, action)


@graph_app.command("neighbors")
def graph_neighbors(
    ctx: typer.Context,
    node_id: str = typer.Option(..., "--node-id"),
    predicate: list[str] | None = typer.Option(None, "--predicate"),
    direction: str = typer.Option("both"),
    limit: int = typer.Option(100, min=1, max=1000),
) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.knowledge.graph_neighbors(
            runtime.context,
            GraphNeighborsRequest(
                node_id=node_id,
                predicates=_split_values(predicate),
                direction=direction,  # type: ignore[arg-type]
                limit=limit,
            ),
        ),
    )


@graph_app.command("path")
def graph_path(
    ctx: typer.Context,
    from_node: str = typer.Option(..., "--from"),
    to_node: str = typer.Option(..., "--to"),
    predicate: list[str] | None = typer.Option(None, "--predicate"),
    max_depth: int = typer.Option(4, min=1, max=8),
) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.knowledge.graph_path(
            runtime.context,
            GraphPathRequest(
                from_node_id=from_node,
                to_node_id=to_node,
                predicates=_split_values(predicate),
                max_depth=max_depth,
            ),
        ),
    )


@review_app.command("list")
def review_list(
    ctx: typer.Context,
    status_value: str = typer.Option("proposed", "--status"),
    limit: int = typer.Option(100, min=1, max=1000),
    predicate: str | None = typer.Option(None, "--predicate"),
    subject_id: str | None = typer.Option(None, "--subject-id"),
) -> None:
    """List candidates for review, ordered by risk then confidence."""
    _run(
        ctx,
        lambda runtime: runtime.container.application.knowledge.candidate_listing(
            runtime.context,
            status_value,
            limit,
            predicate=predicate,
            subject_id=subject_id,
        ),
    )


@review_app.command("propose")
def review_propose(
    ctx: typer.Context,
    subject_id: str = typer.Option(...),
    predicate: str = typer.Option(...),
    object_entity_id: str | None = typer.Option(None),
    object_json: str | None = typer.Option(None, help="JSON literal for a value object"),
    evidence_unit_id: list[str] | None = typer.Option(None),
    origin: str = typer.Option("human"),
    ontology_version: str | None = typer.Option(
        None, help="Defaults to the active catalog version."
    ),
    confidence: float | None = typer.Option(None),
) -> None:
    def action(runtime: Runtime) -> Any:
        object_value: Any = None
        if object_json is not None:
            object_value = json.loads(object_json)
        candidate = AssertionCandidate(
            id=new_id("cand"),
            subject_id=subject_id,
            predicate=predicate,
            object_entity_id=object_entity_id,
            object_value=object_value,
            origin=origin,
            confidence=confidence,
            ontology_version=_resolve_ontology_version(runtime, ontology_version),
            evidence=[
                CandidateEvidence(content_unit_id=value) for value in (evidence_unit_id or [])
            ],
        )
        return runtime.container.application.knowledge.create_candidate(runtime.context, candidate)

    _run(ctx, action)


@review_app.command("approve")
def review_approve(
    ctx: typer.Context,
    candidate_id: str = typer.Argument(...),
    note: str | None = typer.Option(None),
    supersede_contradicted: bool = typer.Option(
        False,
        "--supersede-contradicted",
        help=(
            "Also mark every assertion this candidate contradicts as "
            "superseded by the newly approved assertion"
        ),
    ),
) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.knowledge.review_approve(
            runtime.context,
            candidate_id,
            note,
            supersede_contradicted=supersede_contradicted,
        ),
    )


@review_app.command("revoke")
def review_revoke(
    ctx: typer.Context,
    assertion_id: str = typer.Argument(...),
    note: str = typer.Option(..., "--note", help="Required revocation reason"),
) -> None:
    """Revoke an approved assertion; it leaves all approved-only surfaces."""
    _run(
        ctx,
        lambda runtime: runtime.container.application.knowledge.revoke_assertion(
            runtime.context, assertion_id, note
        ),
    )


@review_app.command("reject")
def review_reject(
    ctx: typer.Context,
    candidate_id: str = typer.Argument(...),
    note: str | None = typer.Option(None),
) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.knowledge.review_reject(
            runtime.context, candidate_id, note
        ),
    )


@jobs_app.command("list")
def jobs_list(
    ctx: typer.Context,
    status_value: str | None = typer.Option(None, "--status"),
    limit: int = typer.Option(100, min=1, max=1000),
) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.operations.list_jobs(
            runtime.context,
            status_value,
            limit,
        ),
    )


@projection_app.command("status")
def projection_status(ctx: typer.Context) -> None:
    """Report projection counts and configured optional backends."""

    def action(runtime: Runtime) -> Any:
        status_report = runtime.container.application.operations.status(runtime.context)
        return {
            "lexical": {
                "content_units": status_report.content_units,
                "indexed_units": status_report.lexical_units,
                "in_sync": status_report.content_units == status_report.lexical_units,
            },
            "graph": {
                "backend": runtime.container.settings.get("graph.backend", "postgres"),
                "approved_assertions": status_report.approved_assertions,
                "canonical_query": True,
            },
            "semantic": {
                "enabled": bool(runtime.container.settings.get("search.semantic_enabled", False)),
                **runtime.container.application.operations.semantic_status(runtime.context),
            },
        }

    _run(ctx, action)


@projection_app.command("rebuild")
def projection_rebuild(
    ctx: typer.Context,
    name: str = typer.Option("lexical", "--name"),
    enqueue: bool = typer.Option(False, "--enqueue"),
) -> None:
    """Rebuild a disposable projection without mutating canonical assertions."""

    def action(runtime: Runtime) -> Any:
        if enqueue:
            job_id = runtime.container.application.operations.enqueue_job(
                runtime.context,
                "rebuild.projection",
                {"projection": name, "workspace": runtime.context.workspace},
                idempotency_key=f"rebuild:{runtime.context.workspace}:{name}",
            )
            return {"projection": name, "job_id": job_id}
        if name in {"semantic", "vector"}:
            return runtime.container.application.retrieval.rebuild_semantic_projection(
                runtime.context
            )
        return runtime.container.application.operations.rebuild_projection(
            runtime.context,
            name,
        )

    _run(ctx, action)


@projection_app.command("verify")
def projection_verify(ctx: typer.Context, name: str = typer.Option("lexical", "--name")) -> None:
    """Run low-cost parity checks for a projection."""

    def action(runtime: Runtime) -> Any:
        report = runtime.container.application.operations.status(runtime.context)
        if name == "lexical":
            ok = report.content_units == report.lexical_units
            return {
                "projection": name,
                "ok": ok,
                "content_units": report.content_units,
                "lexical_units": report.lexical_units,
            }
        if name == "graph":
            return {
                "projection": name,
                "ok": True,
                "backend": runtime.container.settings.get("graph.backend", "postgres"),
                "approved_assertions": report.approved_assertions,
                "note": "the baseline queries canonical PostgreSQL assertions directly",
            }
        if name in {"semantic", "vector"}:
            return runtime.container.application.retrieval.verify_semantic_projection(
                runtime.context
            )
        raise ValidationError(f"unsupported projection verification: {name}")

    _run(ctx, action)


@projection_app.command("activate")
def projection_activate(
    ctx: typer.Context,
    name: str = typer.Option("semantic", "--name"),
    report: Path = typer.Option(
        ...,
        "--report",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Full evaluation report containing lexical and candidate variants",
    ),
    candidate: str = typer.Option(..., "--candidate"),
) -> None:
    """Activate a complete semantic projection after an evaluation gate passes."""

    def action(runtime: Runtime) -> Any:
        if name not in {"semantic", "vector"}:
            raise ValidationError("only semantic projections require explicit activation")
        evaluation = json.loads(report.read_text(encoding="utf-8"))
        decision = validate_activation_report(
            evaluation,
            candidate=candidate,
            configuration=runtime.container.settings.raw,
            code_root=runtime.container.settings.project_root,
        )
        space = runtime.container.application.retrieval.activate_semantic_projection(
            runtime.context
        )
        return {
            "projection": "semantic",
            "status": space.status,
            "space": space,
            "evaluation_run_id": evaluation["run"]["id"],
            "candidate": candidate,
            "decision": decision["decision"],
        }

    _run(ctx, action)


@app.command()
def rebuild(
    ctx: typer.Context,
    projection: str = typer.Option("lexical", "--projection"),
    enqueue: bool = typer.Option(False, "--enqueue"),
) -> None:
    """Backward-compatible alias for `projection rebuild`."""

    def action(runtime: Runtime) -> Any:
        if enqueue:
            job_id = runtime.container.application.operations.enqueue_job(
                runtime.context,
                "rebuild.projection",
                {"projection": projection, "workspace": runtime.context.workspace},
                idempotency_key=f"rebuild:{runtime.context.workspace}:{projection}",
            )
            return {"projection": projection, "job_id": job_id}
        if projection in {"semantic", "vector"}:
            return runtime.container.application.retrieval.rebuild_semantic_projection(
                runtime.context
            )
        return runtime.container.application.operations.rebuild_projection(
            runtime.context,
            projection,
        )

    _run(ctx, action)


@evaluate_app.command("validate")
def evaluate_validate(
    ctx: typer.Context,
    dataset: Path = typer.Option(..., "--dataset", exists=True, dir_okay=False, readable=True),
) -> None:
    def action(runtime: Runtime) -> Any:
        loaded = load_dataset(dataset)
        return {
            "dataset": loaded.name,
            "case_count": len(loaded.cases),
            "categories": sorted({case.category for case in loaded.cases}),
            "lifecycle": loaded.lifecycle,
            "version": loaded.version,
            "gate_eligible": loaded.gate_eligible,
            "required_dimensions": loaded.required_dimensions,
        }

    _run(ctx, action)


@evaluate_app.command("run")
def evaluate_run(
    ctx: typer.Context,
    dataset: Path = typer.Option(..., "--dataset", exists=True, dir_okay=False, readable=True),
    variants: str = typer.Option("lexical", "--variants"),
    output_dir: Path = typer.Option(Path("evaluation/reports"), "--output-dir"),
    warmup_passes: int = typer.Option(1, "--warmup-passes", min=0),
    reviews: Path | None = typer.Option(
        None,
        "--reviews",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Reviewed answer and ontology observations bound to this dataset version",
    ),
) -> None:
    def action(runtime: Runtime) -> Any:
        loaded = load_dataset(dataset)

        def search_case(case: GoldenCase, variant: str) -> list[SearchHit]:
            context = runtime.container.application.operations.request_context(
                workspace=runtime.context.workspace,
                principal_id=case.principal,
                acl_scopes=case.acl_scopes,
            )
            return runtime.container.application.retrieval.search(
                context,
                SearchRequest(query=case.question, limit=case.recall_at),
                mode=variant,
            )

        def enrich_case(case: GoldenCase, hits: list[SearchHit]) -> list[SearchHit]:
            context = runtime.container.application.operations.request_context(
                workspace=runtime.context.workspace,
                principal_id=case.principal,
                acl_scopes=case.acl_scopes,
            )
            enriched: list[SearchHit] = []
            for hit in hits:
                try:
                    evidence = runtime.container.application.evidence.read_unit(
                        context,
                        hit.unit_id,
                    )
                except KipError:
                    enriched.append(hit)
                    continue
                enriched.append(
                    hit.model_copy(
                        update={
                            "metadata": {
                                **hit.metadata,
                                "source_changed_since_index": (evidence.source_changed_since_index),
                            }
                        },
                        deep=True,
                    )
                )
            return enriched

        report = run_evaluation(
            loaded,
            variants=_split_csv(variants),
            search=search_case,
            workspace=runtime.context.workspace,
            dataset_bytes=dataset.read_bytes(),
            configuration=runtime.container.settings.raw,
            code_root=runtime.container.settings.project_root,
            review_bundle=load_review_bundle(reviews) if reviews is not None else None,
            warmup_passes=warmup_passes,
            enrich=(enrich_case if requires_stale_enrichment(loaded) else None),
        )
        paths = write_report(report, output_dir)
        for variant, result in report["variants"].items():
            append_evolution_record(
                output_dir.parent / "evolution.jsonl",
                {
                    "run_id": report["run"]["id"],
                    "variant": variant,
                    "fingerprints": report["fingerprints"],
                    "metrics": result["metrics"],
                    "decision": report["decision"]["status"],
                },
            )
        return {
            "run_id": report["run"]["id"],
            "variants": list(report["variants"]),
            "decision": report["decision"],
            "json_report": str(paths.json_path),
            "markdown_report": str(paths.markdown_path),
        }

    _run(ctx, action)


@evaluate_app.command("compare")
def evaluate_compare(
    ctx: typer.Context,
    report: Path = typer.Option(..., "--report", exists=True, dir_okay=False, readable=True),
    baseline: str = typer.Option("lexical", "--baseline"),
    candidate: str = typer.Option(..., "--candidate"),
) -> None:
    def action(runtime: Runtime) -> Any:
        payload = json.loads(report.read_text(encoding="utf-8"))
        return compare_variants(payload, baseline, candidate)

    _run(ctx, action)


@quality_app.command("validate-manifest")
def quality_validate_manifest(
    ctx: typer.Context,
    manifest: Path = typer.Option(..., "--manifest", exists=True, dir_okay=False, readable=True),
) -> None:
    _run(ctx, lambda runtime: load_experiment(manifest))


@quality_app.command("recommend")
def quality_recommend(
    ctx: typer.Context,
    manifest: Path = typer.Option(..., "--manifest", exists=True, dir_okay=False, readable=True),
    report: Path = typer.Option(..., "--report", exists=True, dir_okay=False, readable=True),
) -> None:
    def action(runtime: Runtime) -> Any:
        return recommend(load_experiment(manifest), load_quality_report(report))

    _run(ctx, action)


@ontology_app.command("validate")
def ontology_validate(
    ctx: typer.Context,
    root: Path = typer.Option(..., "--root", exists=True, file_okay=False, readable=True),
    domain_profile: str = typer.Option("research-project", "--domain-profile"),
) -> None:
    def action(runtime: Runtime) -> Any:
        errors = validate_ontology(root, domain_profile=domain_profile)
        if errors:
            raise ValidationError("invalid ontology contract: " + "; ".join(errors))
        catalog = OntologyCatalog.load(root, domain_profile=domain_profile)
        return {
            "version": catalog.version,
            "domain_profile": catalog.domain_profile,
            "predicate_count": len(catalog.predicates),
        }

    _run(ctx, action)


@ontology_app.command("entities")
def ontology_entities(
    ctx: typer.Context,
    limit: int = typer.Option(100, min=1, max=10_000),
) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.ontology_rag.list_entities(
            runtime.context,
            limit=limit,
        ),
    )


@ontology_app.command("context")
def ontology_context(
    ctx: typer.Context,
    query: str = typer.Argument(...),
    include_candidate_assertions: bool = typer.Option(
        False,
        "--include-candidate-assertions",
        help="Also list proposed (unapproved) candidates, clearly labeled",
    ),
) -> None:
    _run(
        ctx,
        lambda runtime: (
            runtime.container.application.ontology_context.build(
                runtime.context,
                query,
                include_candidates=include_candidate_assertions,
            ).context
        ),
    )


@ontology_app.command("entity-create")
def ontology_entity_create(
    ctx: typer.Context,
    entity_id: str = typer.Option(..., "--id"),
    entity_type: str = typer.Option(..., "--type"),
    name: str = typer.Option(..., "--name"),
    alias: list[str] | None = typer.Option(None, "--alias"),
    acl_scope: list[str] | None = typer.Option(None, "--acl-scope"),
) -> None:
    def action(runtime: Runtime) -> Any:
        return runtime.container.application.ontology_rag.create_entity(
            runtime.context,
            KnowledgeEntity(
                id=entity_id,
                entity_type=entity_type,
                canonical_name=name,
                aliases=_split_values(alias),
                acl_scopes=_split_values(acl_scope),
            ),
        )

    _run(ctx, action)


@ontology_app.command("mine")
def ontology_mine(
    ctx: typer.Context,
    unit_id: list[str] = typer.Option(..., "--unit-id"),
) -> None:
    _run(
        ctx,
        lambda runtime: {
            "job_id": runtime.container.application.ontology_rag.enqueue_mining(
                runtime.context,
                _split_values(unit_id),
            )
        },
    )


@ontology_app.command("candidates")
def ontology_candidates(
    ctx: typer.Context,
    status_value: str = typer.Option("proposed", "--status"),
    limit: int = typer.Option(100, min=1, max=1000),
    predicate: str | None = typer.Option(None, "--predicate"),
    subject_id: str | None = typer.Option(None, "--subject-id"),
) -> None:
    def action(runtime: Runtime) -> Any:
        listing = runtime.container.application.knowledge.candidate_listing(
            runtime.context,
            status_value,
            limit,
            predicate=predicate,
            subject_id=subject_id,
        )
        return {
            "entities": runtime.container.application.ontology_rag.list_entity_candidates(
                runtime.context,
                status=status_value,
                limit=limit,
            ),
            "relations": listing.items,
            "relations_total": listing.total,
        }

    _run(ctx, action)


@ontology_app.command("entity-approve")
def ontology_entity_approve(
    ctx: typer.Context,
    candidate_id: str = typer.Argument(...),
    note: str | None = typer.Option(None),
) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.ontology_rag.approve_entity_candidate(
            runtime.context,
            candidate_id,
            note,
        ),
    )


@ontology_app.command("entity-reject")
def ontology_entity_reject(
    ctx: typer.Context,
    candidate_id: str = typer.Argument(...),
    note: str | None = typer.Option(None),
) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.ontology_rag.reject_entity_candidate(
            runtime.context,
            candidate_id,
            note,
        ),
    )


@ontology_app.command("diff")
def ontology_diff(
    ctx: typer.Context,
    before: Path = typer.Option(..., "--before", exists=True, file_okay=False, readable=True),
    after: Path = typer.Option(..., "--after", exists=True, file_okay=False, readable=True),
    before_domain_profile: str = typer.Option(
        "research-project",
        "--before-domain-profile",
    ),
    after_domain_profile: str = typer.Option(
        "research-project",
        "--after-domain-profile",
    ),
    migration: Path | None = typer.Option(None, "--migration", dir_okay=False, readable=True),
) -> None:
    def action(runtime: Runtime) -> Any:
        result = diff_ontologies(
            before,
            after,
            before_domain_profile=before_domain_profile,
            after_domain_profile=after_domain_profile,
        )
        selected = load_migration(migration) if migration is not None else None
        errors = validate_migration_coverage(result, selected)
        if errors:
            raise ValidationError("invalid ontology migration: " + "; ".join(errors))
        return result

    _run(ctx, action)


@ontology_app.command("migrate-materialize")
def ontology_migrate_materialize(
    ctx: typer.Context,
    before: Path = typer.Option(
        ...,
        "--before",
        exists=True,
        file_okay=False,
        readable=True,
    ),
    after: Path = typer.Option(
        ...,
        "--after",
        exists=True,
        file_okay=False,
        readable=True,
    ),
    migration: Path = typer.Option(
        ...,
        "--migration",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.ontology_migrations.materialize(
            runtime.context,
            before,
            after,
            load_migration(migration),
        ),
    )


@telemetry_app.command("traces")
def telemetry_traces(
    ctx: typer.Context,
    request_id: str | None = typer.Option(None, "--request-id"),
    limit: int = typer.Option(100, "--limit", min=1, max=1000),
) -> None:
    def action(runtime: Runtime) -> Any:
        return runtime.container.application.telemetry.list_traces(
            runtime.context,
            request_id=request_id,
            limit=limit,
        )

    _run(ctx, action)


@telemetry_app.command("prune")
def telemetry_prune(ctx: typer.Context) -> None:
    def action(runtime: Runtime) -> Any:
        return {"deleted": runtime.container.application.telemetry.prune(runtime.context)}

    _run(ctx, action)


def _json_array(value: str, name: str) -> list[object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{name} must be valid JSON") from exc
    if not isinstance(parsed, list):
        raise ValidationError(f"{name} must be a JSON array")
    return parsed


def _validated_interaction_input[InteractionInputModel: BaseModel](
    model: type[InteractionInputModel],
    payload: object,
) -> InteractionInputModel:
    try:
        return model.model_validate(payload)
    except PydanticValidationError as exc:
        raise ValidationError(str(exc)) from exc


@interaction_app.command("clarify")
def interaction_clarify(
    ctx: typer.Context,
    reason: str = typer.Option(..., "--reason"),
    prompt: str = typer.Option(..., "--prompt"),
    choices_json: str = typer.Option("[]", "--choices-json"),
    allow_freeform: bool = typer.Option(
        True,
        "--allow-freeform/--no-allow-freeform",
    ),
    allow_multiple: bool = typer.Option(False, "--allow-multiple"),
    preference_key: str | None = typer.Option(None, "--preference-key"),
) -> None:
    def action(runtime: Runtime) -> Any:
        return runtime.container.application.interactions.create_clarification(
            runtime.context,
            _validated_interaction_input(
                ClarificationRequest,
                {
                    "reason": reason,
                    "prompt": prompt,
                    "choices": _json_array(choices_json, "choices"),
                    "allow_freeform": allow_freeform,
                    "allow_multiple": allow_multiple,
                    "preference_key": preference_key,
                },
            ),
        )

    _run(ctx, action)


@interaction_app.command("answer")
def interaction_answer(
    ctx: typer.Context,
    question_id: str = typer.Option(..., "--question-id"),
    option_id: list[str] | None = typer.Option(None, "--option-id"),
    freeform: str | None = typer.Option(None, "--freeform"),
    remember: bool = typer.Option(False, "--remember"),
) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.interactions.answer_clarification(
            runtime.context,
            _validated_interaction_input(
                ClarificationAnswer,
                {
                    "question_id": question_id,
                    "option_ids": option_id or [],
                    "freeform": freeform,
                    "remember": remember,
                },
            ),
        ),
    )


@interaction_app.command("preferences")
def interaction_preferences(ctx: typer.Context) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.interactions.list_preferences(
            runtime.context
        ),
    )


@interaction_app.command("remember")
def interaction_remember(
    ctx: typer.Context,
    key: str = typer.Option(..., "--key"),
    value: list[str] | None = typer.Option(None, "--value"),
    confirmed: bool = typer.Option(False, "--confirmed"),
) -> None:
    def action(runtime: Runtime) -> Any:
        if not confirmed:
            raise ValidationError("--confirmed is required to persist a preference")
        return runtime.container.application.interactions.save_preference(
            runtime.context,
            _validated_interaction_input(
                UserPreferenceWrite,
                {
                    "key": key,
                    "values": value or [],
                    "confirmed": True,
                },
            ),
        )

    _run(ctx, action)


@interaction_app.command("forget")
def interaction_forget(
    ctx: typer.Context,
    key: str = typer.Option(..., "--key"),
) -> None:
    _run(
        ctx,
        lambda runtime: {
            "deleted": runtime.container.application.interactions.delete_preference(
                runtime.context,
                key,
            )
        },
    )


@interaction_app.command("feedback")
def interaction_feedback(
    ctx: typer.Context,
    outcome: str = typer.Option(..., "--outcome"),
    reason_code: list[str] | None = typer.Option(None, "--reason-code"),
    request_id: str | None = typer.Option(None, "--request-id"),
) -> None:
    _run(
        ctx,
        lambda runtime: runtime.container.application.interactions.submit_feedback(
            runtime.context,
            _validated_interaction_input(
                FeedbackSubmission,
                {
                    "request_id": request_id,
                    "outcome": outcome,
                    "reason_codes": reason_code or [],
                },
            ),
        ),
    )


@interaction_app.command("prune")
def interaction_prune(ctx: typer.Context) -> None:
    def action(runtime: Runtime) -> Any:
        return {
            "deleted": runtime.container.application.interactions.prune_expired_clarifications(
                runtime.context
            )
        }

    _run(ctx, action)


@ontology_discovery_app.command("propose")
def ontology_discovery_propose(
    ctx: typer.Context,
    kind: str = typer.Option(..., "--kind"),
    symbol: str = typer.Option(..., "--symbol"),
    label: str = typer.Option(..., "--label"),
    definition: str = typer.Option(..., "--definition"),
    target_symbol: str | None = typer.Option(None, "--target-symbol"),
    parent: str | None = typer.Option(
        None, "--parent", help="entity_type only: the proposed parent type."
    ),
    domain_type: list[str] | None = typer.Option(
        None, "--domain", help="predicate only: allowed subject types."
    ),
    range_type: list[str] | None = typer.Option(
        None, "--range", help="predicate only: allowed object types."
    ),
    inverse: str | None = typer.Option(None, "--inverse", help="predicate only."),
    risk: str | None = typer.Option(None, "--risk", help="predicate only: low|medium|high."),
    review: str | None = typer.Option(
        None, "--review", help="predicate only: not_required|conditional|required."
    ),
    extraction: str | None = typer.Option(None, "--extraction", help="predicate only."),
    confirmed: bool = typer.Option(False, "--confirmed"),
) -> None:
    def action(runtime: Runtime) -> Any:
        if not confirmed:
            raise ValidationError("--confirmed is required to propose ontology discovery")
        return runtime.container.application.interactions.propose_ontology_discovery(
            runtime.context,
            _validated_interaction_input(
                OntologyDiscoveryProposal,
                {
                    "kind": kind,
                    "symbol": symbol,
                    "label": label,
                    "definition": definition,
                    "target_symbol": target_symbol,
                    "parent": parent,
                    "domain": domain_type or None,
                    "range": range_type or None,
                    "inverse": inverse,
                    "risk": risk,
                    "review": review,
                    "extraction": extraction,
                    "confirmed": True,
                },
            ),
        )

    _run(ctx, action)


@ontology_discovery_app.command("list")
def ontology_discovery_list(
    ctx: typer.Context,
    status: str | None = typer.Option("proposed", "--status"),
    limit: int = typer.Option(100, "--limit", min=1, max=1000),
) -> None:
    _run(
        ctx,
        lambda runtime: (
            runtime.container.application.interactions.list_ontology_discovery_candidates(
                runtime.context,
                status=status,
                limit=limit,
            )
        ),
    )


@ontology_discovery_app.command("review")
def ontology_discovery_review(
    ctx: typer.Context,
    candidate_id: str = typer.Option(..., "--candidate-id"),
    action: str = typer.Option(..., "--action"),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    _run(
        ctx,
        lambda runtime: (
            runtime.container.application.interactions.review_ontology_discovery_candidate(
                runtime.context,
                candidate_id,
                _validated_interaction_input(
                    OntologyDiscoveryReview,
                    {"action": action, "note": note},
                ),
            )
        ),
    )


@export_app.command("canonical")
def export_canonical(
    ctx: typer.Context,
    output: Path = typer.Option(Path("exports/canonical.jsonl"), "--output"),
) -> None:
    """Export canonical records as deterministic JSONL for migration or backup."""
    _run(
        ctx,
        lambda runtime: runtime.container.application.operations.export_canonical(
            runtime.context,
            output,
        ),
    )


@app.command(name="export-file", hidden=True)
def export_file_alias(
    ctx: typer.Context,
    output: Path = typer.Option(Path("exports/canonical.jsonl"), "--output"),
) -> None:
    """Legacy alias retained for scripts created before v3.1."""
    _run(
        ctx,
        lambda runtime: runtime.container.application.operations.export_canonical(
            runtime.context,
            output,
        ),
    )


@api_app.command("serve")
def api_serve(
    ctx: typer.Context,
    host: str | None = typer.Option(None),
    port: int | None = typer.Option(None),
) -> None:
    runtime = _runtime(ctx)
    try:
        import uvicorn
    except ImportError as exc:
        _emit_error(runtime, exc)
        raise typer.Exit(code=2) from exc
    uvicorn.run(
        "kip.api:create_app_from_environment",
        factory=True,
        host=host or runtime.container.settings.api_host,
        port=port or runtime.container.settings.api_port,
        reload=False,
    )


@worker_app.command("run")
def worker_run(
    ctx: typer.Context,
    once: bool = typer.Option(False, "--once"),
    poll_seconds: float = typer.Option(2.0, min=0.1, max=60),
) -> None:
    runtime = _runtime(ctx)
    from kip.worker import run_worker

    run_worker(runtime.container, once=once, poll_seconds=poll_seconds)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
