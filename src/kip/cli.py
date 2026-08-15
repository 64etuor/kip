from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from kip.adapters.ocr.kordoc import KordocOcrConfig, probe_kordoc_version
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
    StatusReport,
)
from kip.errors import AuthorizationError, KipError, NotFoundError, ValidationError, error_code
from kip.evaluation.drafts import promote_draft, record_draft_review_decision, validate_draft
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
from kip.ontology_discovery_release import RELEASE_JOURNAL_FILENAME
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
evaluate_draft_app = typer.Typer(
    no_args_is_help=True,
    help="Judge-proposed golden case drafts (sample-audit gate before promotion)",
)
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
evaluate_app.add_typer(evaluate_draft_app, name="draft")
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


def _emit(runtime: Runtime, data: Any, *, warnings: list[str] | None = None) -> None:
    envelope = Envelope(
        ok=True,
        data=data.model_dump(mode="json") if hasattr(data, "model_dump") else data,
        meta=EnvelopeMeta(
            request_id=runtime.context.request_id or new_id("req"),
            workspace=runtime.context.workspace,
            warnings=warnings or [],
        ),
    )
    typer.echo(envelope.model_dump_json(indent=2))


def _error_message(exc: BaseException) -> str:
    """Human-readable error message; `forbidden` gains actionable guidance.

    `error_code(exc)` stays exactly `forbidden` for an `AuthorizationError`
    (every edge — CLI, REST, MCP — must keep agreeing on that machine
    code). The operator's natural first fix, `kip <cmd> --role admin`,
    fails with "No such option: --role" because `--role`/`--roles` are
    root-level options that must precede the subcommand, so only the
    human-readable message gains a hint pointing at the actual fix.
    """
    message = str(exc)
    if isinstance(exc, AuthorizationError):
        message += (
            " — put --role admin BEFORE the subcommand "
            "(./scripts/kip --role admin review approve <id>) or set KIP_ROLES=admin"
        )
    return message


def _emit_error(runtime: Runtime | None, exc: BaseException) -> None:
    context = runtime.context if runtime else RequestContext(request_id=new_id("req"))
    envelope = Envelope(
        ok=False,
        error=ErrorInfo(code=error_code(exc), message=_error_message(exc)),
        meta=EnvelopeMeta(
            request_id=context.request_id or new_id("req"),
            workspace=context.workspace,
        ),
    )
    typer.echo(envelope.model_dump_json(indent=2), err=True)


def _clean_validation_message(exc: PydanticValidationError) -> str:
    """A one-line, envelope-safe message for a raw Pydantic validation error.

    `str(exc)` renders a multi-line blob with a pydantic.dev documentation
    URL per error, which is unsuitable for a JSON envelope `message` field.
    """
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ()))
        message = str(error.get("msg", "invalid value"))
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts) or "validation failed"


def _validated_existing_path(
    path: Path,
    option_name: str,
    *,
    dir_okay: bool = False,
) -> Path:
    """Validate a Path CLI option's existence inside the command body.

    Click's `exists=True` (and sibling `file_okay`/`dir_okay`/`readable`)
    filesystem checks run while Click parses arguments, before `_run` wraps
    the command in a versioned JSON envelope — AGENTS.md requires every CLI
    command to emit one. A missing or otherwise invalid path there prints
    raw Click usage text on stderr with exit code 2 instead of an
    `ok: false` envelope. Every `Path` option that used to declare those
    Click-level checks now stays a plain `Path` option and calls this
    helper first, so a bad path is reported the same way as any other
    validation failure.
    """
    kind = "directory" if dir_okay else "file"
    if not path.exists():
        raise NotFoundError(
            f"{kind} not found: {path} ({option_name} expects an existing {kind} path)"
        )
    if dir_okay and not path.is_dir():
        raise ValidationError(f"{option_name} expects a directory, got a file: {path}")
    if not dir_okay and not path.is_file():
        raise ValidationError(f"{option_name} expects a file, got a directory: {path}")
    if not os.access(path, os.R_OK):
        raise ValidationError(f"{option_name} path is not readable: {path}")
    return path


def _run(
    ctx: typer.Context,
    function: Callable[[Runtime], Any],
    *,
    warnings: Callable[[Any], list[str]] | None = None,
) -> None:
    runtime: Runtime | None = None
    try:
        runtime = _runtime(ctx)
        result = function(runtime)
        _emit(runtime, result, warnings=warnings(result) if warnings is not None else None)
    except KipError as exc:
        _emit_error(runtime, exc)
        raise typer.Exit(code=4 if isinstance(exc, NotFoundError) else 3) from exc
    except PydanticValidationError as exc:
        # A request model (e.g. SearchRequest) can raise this directly from a
        # field validator. Map it the same way KipError's ValidationError is
        # mapped, so every edge reports the same code and a clean message.
        _emit_error(runtime, ValidationError(_clean_validation_message(exc)))
        raise typer.Exit(code=3) from exc
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
    configured = runtime.container.application.ingestion.enabled_sync_sources()
    raise ValidationError(
        f"unknown source: {source}. Configured sources: {', '.join(configured) or 'none'}."
    )


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
        for name in runtime.container.application.ingestion.enabled_sync_sources():
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
    # `_resolve_sync_source` only ever returns a filesystem source name, "all"
    # (handled above), or one of the known remote source names; anything else
    # already raised ValidationError there. Dispatch generically so adding a
    # remote connector requires no edit here.
    return runtime.container.application.ingestion.sync_remote(
        runtime.context, selected, since=since
    )


@app.command()
def capabilities(ctx: typer.Context) -> None:
    """Report available parsers, connectors, projections, and edge adapters."""
    _run(
        ctx,
        lambda runtime: runtime.container.application.operations.capabilities(runtime.context),
    )


def _status_summary(report: StatusReport) -> str:
    """One-line Korean plain-language verdict for `status --summary`.

    `StatusReport` is a versioned contract model (checked by
    `scripts/generate_contracts.py --check`), so a plain-language field
    cannot be added to it without a contract change. Instead this is
    attached to the envelope's existing `meta.warnings` list, and only
    when the operator opts in with `--summary` — the default `status`
    output is unchanged.
    """
    lexical_gap = report.content_units - report.lexical_units
    notes: list[str] = []
    if report.failed_jobs:
        notes.append(
            f"문제: 실패한 작업 {report.failed_jobs}건이 있습니다. "
            "`kip jobs list --status failed`로 확인하세요."
        )
    if lexical_gap:
        notes.append(
            f"경고: 검색 색인이 원본보다 {lexical_gap}건 뒤처져 있습니다 "
            f"(콘텐츠 {report.content_units}건 / 색인 {report.lexical_units}건)."
        )
    if notes:
        return " ".join(notes)
    return (
        f"정상: 콘텐츠 {report.content_units}건 모두 색인됨, "
        f"승인된 사실 {report.approved_assertions}건, 대기 작업 {report.queued_jobs}건."
    )


@app.command()
def status(
    ctx: typer.Context,
    summary: bool = typer.Option(
        False,
        "--summary",
        help="Also add a one-line Korean plain-language verdict to meta.warnings",
    ),
) -> None:
    """Report canonical and projection counts."""

    def action(runtime: Runtime) -> Any:
        return runtime.container.application.operations.status(runtime.context)

    _run(
        ctx,
        action,
        warnings=(lambda report: [_status_summary(report)]) if summary else None,
    )


# A doctor-only timeout: this check must stay fast even when
# parsers.ocr.timeout_seconds is configured generously for real OCR runs.
_KORDOC_DOCTOR_PROBE_TIMEOUT_SECONDS = 5


def _kordoc_ocr_doctor_check(settings: Settings) -> dict[str, Any]:
    """Report whether the configured Kordoc OCR runtime is resolvable.

    Reuses :func:`probe_kordoc_version`, the same version-resolution policy
    the OCR adapter enforces before every ``recognize`` call, so this check
    and the adapter can never disagree about what counts as a usable Kordoc
    runtime. Not required: parsing still succeeds (in degraded ``partial``
    mode with ``OCR_FAILED`` warnings) without it, so this is a WARN-level
    signal that surfaces the degradation before a real sync hits it.
    """
    kordoc_config = settings.get("parsers.ocr.kordoc", {}) or {}
    enabled = isinstance(kordoc_config, dict) and bool(kordoc_config.get("enabled", False))
    if not enabled:
        return {
            "name": "kordoc_ocr_resolvable",
            "ok": True,
            "required": False,
            "details": {"enabled": False, "version": None, "reason": None},
        }
    probe = probe_kordoc_version(
        KordocOcrConfig(
            argv=tuple(str(item) for item in kordoc_config.get("argv", [])),
            version_argv=tuple(str(item) for item in kordoc_config.get("version_argv", [])),
            expected_version=str(kordoc_config.get("expected_version", "4.7.3")),
            timeout_seconds=_KORDOC_DOCTOR_PROBE_TIMEOUT_SECONDS,
        )
    )
    reason = (
        None
        if probe.ok
        else (
            f"kordoc enabled but not resolvable on PATH ({probe.error}); "
            "image-bearing PDF/PPTX will degrade to partial; run "
            "scripts/install-kordoc.sh or disable parsers.ocr.kordoc"
        )
    )
    return {
        "name": "kordoc_ocr_resolvable",
        "ok": probe.ok,
        "required": False,
        "details": {"enabled": True, "version": probe.version, "reason": reason},
    }


def _doctor_summary(checks: list[dict[str, Any]], required_failures: list[str]) -> str:
    """One-line Korean plain-language verdict for the `doctor` payload.

    `doctor`'s `checks` list (`content_units`, `lexical_units`,
    `semantic_projection_status`-shaped detail dicts, ...) is meaningful to
    an operator who already knows the system, not to a non-expert. This is
    a plain `dict` field (not a versioned contract model), so adding it
    here does not need a contract regeneration.
    """
    required_total = sum(1 for item in checks if item["required"])
    required_ok = required_total - len(required_failures)
    if required_failures:
        return (
            f"문제: 필수 점검 {len(required_failures)}건 실패 ({', '.join(required_failures)}). "
            "아래 checks 항목의 reason을 확인하세요."
        )
    optional_warnings = [item for item in checks if not item["required"] and not item["ok"]]
    if not optional_warnings:
        return f"정상: 필수 점검 {required_ok}/{required_total} 통과."
    first = optional_warnings[0]
    details = first.get("details")
    reason = details.get("reason") if isinstance(details, dict) else None
    hint = f"{first['name']}" + (f" — {reason}" if reason else "")
    return f"정상: 필수 점검 {required_ok}/{required_total} 통과. 경고 {len(optional_warnings)}건({hint})."


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
        checks.append(_kordoc_ocr_doctor_check(settings))
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
        ontology_root = settings.project_root / "ontology"
        adaptive_discovery = bool(settings.get("ontology.adaptive_discovery", False))
        ontology_root_writable = ontology_root.is_dir() and os.access(ontology_root, os.W_OK)
        ontology_check_ok = (not adaptive_discovery) or ontology_root_writable
        checks.append(
            {
                "name": "ontology_adaptive_discovery_writable",
                "ok": ontology_check_ok,
                # Only a real blocker once adaptive discovery is on: approval
                # of a discovery candidate writes the release and fails
                # closed if the root is missing or not writable.
                "required": adaptive_discovery,
                "details": {
                    "path": str(ontology_root),
                    "adaptive_discovery_enabled": adaptive_discovery,
                    "reason": None
                    if ontology_check_ok
                    else (
                        "ontology.adaptive_discovery is enabled but the ontology "
                        "root is missing or not writable; discovery approval "
                        "will fail closed"
                    ),
                },
            }
        )
        pending_release_path = ontology_root / RELEASE_JOURNAL_FILENAME
        pending_release_exists = pending_release_path.exists()
        checks.append(
            {
                "name": "ontology_pending_release_journal",
                "ok": not pending_release_exists,
                "required": False,
                "details": {
                    "path": str(pending_release_path),
                    "reason": None
                    if not pending_release_exists
                    else (
                        "a pending ontology release journal was found; a prior "
                        "release may have crashed mid-write and will be healed "
                        "on the next container start-up or materialization"
                    ),
                },
            }
        )
        required_failures = [item["name"] for item in checks if item["required"] and not item["ok"]]
        return {
            "healthy": not required_failures,
            "required_failures": required_failures,
            "checks": checks,
            "capabilities": capabilities.model_dump(mode="json"),
            "summary": _doctor_summary(checks, required_failures),
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
    max_chars: int = typer.Option(120000, min=1000, max=200000),
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
    max_chars: int = typer.Option(32000, min=1000, max=200000),
    mode: str | None = typer.Option(None, "--mode"),
    source_kind: list[str] | None = typer.Option(None, "--source-kind"),
    document_type: list[str] | None = typer.Option(None, "--document-type"),
    project_id: list[str] | None = typer.Option(None, "--project-id"),
    include_candidate_assertions: bool = typer.Option(
        False,
        "--include-candidate-assertions",
    ),
) -> None:
    """Get one evidence-backed answer with citations for a question.

    Use `search` to find documents, `context` to build a raw evidence pack
    for your own reasoning, `answer` when you want a direct, cited answer
    instead, and `read` to read one evidence unit verbatim.
    """

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
    """Get one canonical artifact record (a stored file/blob) by its ID."""
    _run(
        ctx,
        lambda runtime: runtime.container.application.evidence.get_artifact(
            runtime.context,
            artifact_id,
        ),
    )


@get_app.command("document")
def get_document(ctx: typer.Context, document_id: str = typer.Argument(...)) -> None:
    """Get one canonical document record by its ID."""
    _run(
        ctx,
        lambda runtime: runtime.container.application.evidence.get_document(
            runtime.context,
            document_id,
        ),
    )


@get_app.command("candidate")
def get_candidate(ctx: typer.Context, candidate_id: str = typer.Argument(...)) -> None:
    """Get one candidate assertion by its ID, whatever its review status."""
    _run(
        ctx,
        lambda runtime: runtime.container.application.knowledge.get_candidate(
            runtime.context,
            candidate_id,
        ),
    )


@get_app.command("assertion")
def get_assertion(ctx: typer.Context, assertion_id: str = typer.Argument(...)) -> None:
    """Get one approved assertion by its ID."""
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
    """Synchronize every enabled source now (filesystem, Slack, mail); use for a full refresh."""
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
    """Synchronize one configured filesystem (NAS) source."""
    _run(ctx, lambda runtime: _sync_one(runtime, source, enqueue=enqueue, dry_run=dry_run))


@sync_app.command("slack")
def sync_slack(ctx: typer.Context, oldest: str | None = typer.Option(None)) -> None:
    """Synchronize the configured Slack workspace."""
    _run(ctx, lambda runtime: _sync_one(runtime, "slack", since=oldest))


@sync_app.command("imap")
def sync_imap(ctx: typer.Context) -> None:
    """Synchronize the configured IMAP mailbox."""
    _run(ctx, lambda runtime: _sync_one(runtime, "imap"))


@sync_app.command("apple-mail")
def sync_apple_mail(ctx: typer.Context) -> None:
    """Synchronize the configured Apple Mail account."""
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
    """Re-run parsing for a filesystem source into a shadow extraction (safe by default).

    Without `--activate` nothing changes; the previous active extraction stays
    live until every safety gate passes and `--activate` replaces it atomically.
    """

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
    """Read an exact cell range from one XLSX artifact; never estimate totals from search."""
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
    """List approved assertions directly connected to a node."""
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
    """Find an approved-assertion path between two nodes."""
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
    """Propose a candidate assertion (subject/predicate/object) for human review."""

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
    """Approve a candidate assertion, promoting it to an approved fact (requires admin role)."""
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
    """Revoke an approved assertion; it leaves all approved-only surfaces (requires admin role)."""
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
    """Reject a candidate assertion so it is not promoted (requires admin role)."""
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
    """List durable background jobs and their status."""
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
        capabilities = runtime.container.application.operations.capabilities(runtime.context)
        return {
            "lexical": {
                "content_units": status_report.content_units,
                "indexed_units": status_report.lexical_units,
                "in_sync": status_report.content_units == status_report.lexical_units,
            },
            "graph": {
                "backend": capabilities.graph_backend,
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
            capabilities = runtime.container.application.operations.capabilities(runtime.context)
            return {
                "projection": name,
                "ok": True,
                "backend": capabilities.graph_backend,
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
        help="Full evaluation report containing lexical and candidate variants",
    ),
    candidate: str = typer.Option(..., "--candidate"),
) -> None:
    """Activate a complete semantic projection after an evaluation gate passes."""

    def action(runtime: Runtime) -> Any:
        if name not in {"semantic", "vector"}:
            raise ValidationError("only semantic projections require explicit activation")
        report_path = _validated_existing_path(report, "--report")
        evaluation = json.loads(report_path.read_text(encoding="utf-8"))
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
    dataset: Path = typer.Option(..., "--dataset"),
) -> None:
    """Validate a golden dataset file's structure without running it."""

    def action(runtime: Runtime) -> Any:
        loaded = load_dataset(_validated_existing_path(dataset, "--dataset"))
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
    dataset: Path = typer.Option(..., "--dataset"),
    variants: str = typer.Option("lexical", "--variants"),
    output_dir: Path = typer.Option(Path("evaluation/reports"), "--output-dir"),
    warmup_passes: int = typer.Option(1, "--warmup-passes", min=0),
    reviews: Path | None = typer.Option(
        None,
        "--reviews",
        help="Reviewed answer and ontology observations bound to this dataset version",
    ),
) -> None:
    """Run retrieval quality evaluation for one or more variants against a golden dataset."""

    def action(runtime: Runtime) -> Any:
        dataset_path = _validated_existing_path(dataset, "--dataset")
        loaded = load_dataset(dataset_path)

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

        review_bundle = None
        if reviews is not None:
            review_bundle = load_review_bundle(_validated_existing_path(reviews, "--reviews"))
        report = run_evaluation(
            loaded,
            variants=_split_csv(variants),
            search=search_case,
            workspace=runtime.context.workspace,
            dataset_bytes=dataset_path.read_bytes(),
            configuration=runtime.container.settings.raw,
            code_root=runtime.container.settings.project_root,
            review_bundle=review_bundle,
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
    report: Path = typer.Option(..., "--report"),
    baseline: str = typer.Option("lexical", "--baseline"),
    candidate: str = typer.Option(..., "--candidate"),
) -> None:
    """Compare two variants from an existing evaluation report."""

    def action(runtime: Runtime) -> Any:
        report_path = _validated_existing_path(report, "--report")
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        return compare_variants(payload, baseline, candidate)

    _run(ctx, action)


@evaluate_draft_app.command("validate")
def evaluate_draft_validate(
    ctx: typer.Context,
    draft: Path = typer.Option(..., "--draft"),
) -> None:
    """Validate a judge-proposed golden case draft before sample-audit review."""

    def action(runtime: Runtime) -> Any:
        return validate_draft(_validated_existing_path(draft, "--draft"))

    _run(ctx, action)


@evaluate_draft_app.command("review")
def evaluate_draft_review(
    ctx: typer.Context,
    draft: Path = typer.Option(..., "--draft"),
    review: Path = typer.Option(..., "--review", dir_okay=False),
    case_id: str = typer.Option(..., "--case-id"),
    decision: str = typer.Option(..., "--action", help="approve|reject"),
    reviewer: str = typer.Option(..., "--reviewer"),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    """Record a reviewer's approve/reject decision for one drafted golden case."""

    def action(runtime: Runtime) -> Any:
        return record_draft_review_decision(
            draft_path=_validated_existing_path(draft, "--draft"),
            review_path=review,
            case_id=case_id,
            action=decision,
            reviewer=reviewer,
            note=note,
        )

    _run(ctx, action)


@evaluate_draft_app.command("promote")
def evaluate_draft_promote(
    ctx: typer.Context,
    draft: Path = typer.Option(..., "--draft"),
    review: Path = typer.Option(..., "--review"),
    dataset: Path = typer.Option(..., "--dataset", dir_okay=False),
    min_sample_rate: float = typer.Option(0.2, "--min-sample-rate", min=0.0, max=1.0),
    lifecycle: str = typer.Option(
        "reviewed", "--lifecycle", help="Canonical-authority lifecycle assigned to promoted cases"
    ),
    dataset_version: str | None = typer.Option(
        None,
        "--dataset-version",
        help="Dataset/case version assigned by promotion; required for a fresh dataset "
        "or when the target dataset has no non-draft version",
    ),
    source_revision: str | None = typer.Option(
        None,
        "--source-revision",
        help="Source revision assigned by promotion; defaults to the draft's corpus_fingerprint",
    ),
) -> None:
    """Promote reviewed draft golden cases into the dataset once the sample-audit gate passes."""

    def action(runtime: Runtime) -> Any:
        return promote_draft(
            draft_path=_validated_existing_path(draft, "--draft"),
            review_path=_validated_existing_path(review, "--review"),
            dataset_path=dataset,
            min_sample_rate=min_sample_rate,
            lifecycle=lifecycle,
            dataset_version=dataset_version,
            source_revision=source_revision,
        )

    _run(ctx, action)


@quality_app.command("validate-manifest")
def quality_validate_manifest(
    ctx: typer.Context,
    manifest: Path = typer.Option(..., "--manifest"),
) -> None:
    """Validate a version-pinned experiment manifest."""
    _run(ctx, lambda runtime: load_experiment(_validated_existing_path(manifest, "--manifest")))


@quality_app.command("recommend")
def quality_recommend(
    ctx: typer.Context,
    manifest: Path = typer.Option(..., "--manifest"),
    report: Path = typer.Option(..., "--report"),
) -> None:
    """Recommend a promotion decision from an experiment manifest and an evaluation report."""

    def action(runtime: Runtime) -> Any:
        manifest_path = _validated_existing_path(manifest, "--manifest")
        report_path = _validated_existing_path(report, "--report")
        return recommend(load_experiment(manifest_path), load_quality_report(report_path))

    _run(ctx, action)


@ontology_app.command("validate")
def ontology_validate(
    ctx: typer.Context,
    root: Path = typer.Option(..., "--root"),
    domain_profile: str = typer.Option("research-project", "--domain-profile"),
) -> None:
    """Validate an ontology contract directory against its domain profile."""

    def action(runtime: Runtime) -> Any:
        root_path = _validated_existing_path(root, "--root", dir_okay=True)
        errors = validate_ontology(root_path, domain_profile=domain_profile)
        if errors:
            raise ValidationError("invalid ontology contract: " + "; ".join(errors))
        catalog = OntologyCatalog.load(root_path, domain_profile=domain_profile)
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
    """List known knowledge entities."""
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
    """Build an ontology-aware context summary (entities and relations) for a query."""
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
    """Create a new knowledge entity by hand (requires admin role)."""

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
    """Enqueue relation mining over evidence units for new candidates (requires admin role)."""
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
    """List proposed entity and relation candidates awaiting review."""

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
    """Approve a proposed entity candidate as a canonical entity (requires admin role)."""
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
    """Reject a proposed entity candidate (requires admin role)."""
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
    before: Path = typer.Option(..., "--before"),
    after: Path = typer.Option(..., "--after"),
    before_domain_profile: str = typer.Option(
        "research-project",
        "--before-domain-profile",
    ),
    after_domain_profile: str = typer.Option(
        "research-project",
        "--after-domain-profile",
    ),
    migration: Path | None = typer.Option(None, "--migration", dir_okay=False),
) -> None:
    """Diff two ontology contract versions and validate migration coverage."""

    def action(runtime: Runtime) -> Any:
        before_path = _validated_existing_path(before, "--before", dir_okay=True)
        after_path = _validated_existing_path(after, "--after", dir_okay=True)
        result = diff_ontologies(
            before_path,
            after_path,
            before_domain_profile=before_domain_profile,
            after_domain_profile=after_domain_profile,
        )
        migration_path = (
            _validated_existing_path(migration, "--migration") if migration is not None else None
        )
        selected = load_migration(migration_path) if migration_path is not None else None
        errors = validate_migration_coverage(result, selected)
        if errors:
            raise ValidationError("invalid ontology migration: " + "; ".join(errors))
        return result

    _run(ctx, action)


@ontology_app.command("migrate-materialize")
def ontology_migrate_materialize(
    ctx: typer.Context,
    before: Path = typer.Option(..., "--before"),
    after: Path = typer.Option(..., "--after"),
    migration: Path = typer.Option(..., "--migration"),
) -> None:
    """Materialize an ontology migration between a before and after ontology version."""

    def action(runtime: Runtime) -> Any:
        before_path = _validated_existing_path(before, "--before", dir_okay=True)
        after_path = _validated_existing_path(after, "--after", dir_okay=True)
        migration_path = _validated_existing_path(migration, "--migration")
        return runtime.container.application.ontology_migrations.materialize(
            runtime.context,
            before_path,
            after_path,
            load_migration(migration_path),
        )

    _run(ctx, action)


@telemetry_app.command("traces")
def telemetry_traces(
    ctx: typer.Context,
    request_id: str | None = typer.Option(None, "--request-id"),
    limit: int = typer.Option(100, "--limit", min=1, max=1000),
) -> None:
    """List redacted RAG query traces for debugging retrieval quality (requires admin role)."""

    def action(runtime: Runtime) -> Any:
        return runtime.container.application.telemetry.list_traces(
            runtime.context,
            request_id=request_id,
            limit=limit,
        )

    _run(ctx, action)


@telemetry_app.command("prune")
def telemetry_prune(ctx: typer.Context) -> None:
    """Delete query traces past their retention window (requires admin role)."""

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
    """Ask a bounded clarification question when a request is genuinely ambiguous."""

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
    """Answer a pending clarification question raised by `interaction clarify`."""
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
    """List saved user preferences."""
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
    """Persist a confirmed user preference (pass --confirmed to actually save it)."""

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
    """Delete a saved user preference by key."""
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
    """Submit outcome feedback (e.g. helpful/not helpful) for a prior request."""
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
    """Delete expired clarification questions that were never answered."""

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
    """Propose a new non-activating ontology candidate (entity type or predicate) for review."""

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
    """List proposed ontology discovery candidates."""
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
    """Approve or reject an ontology discovery candidate (requires admin role)."""
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
    """Run the optional REST API adapter (same application services as the CLI)."""
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
    """Run the background worker that drains durable jobs (sync, rebuild, mining, ...)."""
    runtime = _runtime(ctx)
    from kip.worker import run_worker

    run_worker(runtime.container, once=once, poll_seconds=poll_seconds)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
