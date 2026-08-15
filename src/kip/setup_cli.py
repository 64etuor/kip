from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, cast

import typer
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from kip.domain.json_types import JsonValue
from kip.domain.models import Envelope, EnvelopeMeta, ErrorInfo
from kip.errors import (
    AuthorizationError,
    ConflictError,
    KipError,
    NotFoundError,
    ValidationError,
)
from kip.ids import new_id
from kip.setup.service import SetupService

setup_app = typer.Typer(
    no_args_is_help=True,
    help="Plan and apply a safe KIP deployment before runtime startup",
)


class SetupCommandContext:
    def __init__(self, service: SetupService) -> None:
        self.service = service


@setup_app.callback()
def setup_root(
    ctx: typer.Context,
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
    state: Annotated[Path, typer.Option("--state")] = Path(
        ".kip/setup-state.json"
    ),
) -> None:
    root = project_root.expanduser().resolve()
    state_path = state if state.is_absolute() else root / state
    ctx.obj = SetupCommandContext(
        SetupService(project_root=root, state_path=state_path)
    )


@setup_app.command("inspect")
def setup_inspect(
    ctx: typer.Context,
    project_root: Annotated[
        Path | None,
        typer.Option("--project-root"),
    ] = None,
    state: Annotated[Path | None, typer.Option("--state")] = None,
) -> None:
    """Show the next unanswered setup question and what is still missing."""
    _run_setup(
        ctx,
        lambda service: service.inspect(),
        project_root=project_root,
        state=state,
    )


@setup_app.command("answer")
def setup_answer(
    ctx: typer.Context,
    question: Annotated[str, typer.Option("--question")],
    value: Annotated[str, typer.Option("--value")],
    project_root: Annotated[
        Path | None,
        typer.Option("--project-root"),
    ] = None,
    state: Annotated[Path | None, typer.Option("--state")] = None,
) -> None:
    """Record one answer to the question `setup inspect` just asked."""
    _run_setup(
        ctx,
        lambda service: service.record_answer(question, value),
        project_root=project_root,
        state=state,
    )


@setup_app.command("preview")
def setup_preview(
    ctx: typer.Context,
    project_root: Annotated[
        Path | None,
        typer.Option("--project-root"),
    ] = None,
    state: Annotated[Path | None, typer.Option("--state")] = None,
) -> None:
    """Preview what would be collected (file counts, sizes, exclusions) before planning."""
    _run_setup(
        ctx,
        lambda service: service.preview(),
        project_root=project_root,
        state=state,
    )


@setup_app.command("plan")
def setup_plan(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option("--output")] = Path(
        ".kip/setup-plan.json"
    ),
    project_root: Annotated[
        Path | None,
        typer.Option("--project-root"),
    ] = None,
    state: Annotated[Path | None, typer.Option("--state")] = None,
) -> None:
    """Write the reviewable setup plan file a human approves before `setup apply`."""

    def action(service: SetupService) -> object:
        target = output if output.is_absolute() else service.project_root / output
        return service.write_plan(target)

    _run_setup(
        ctx,
        action,
        project_root=project_root,
        state=state,
    )


@setup_app.command("apply")
def setup_apply(
    ctx: typer.Context,
    plan: Annotated[Path, typer.Option("--plan")],
    project_root: Annotated[
        Path | None,
        typer.Option("--project-root"),
    ] = None,
    state: Annotated[Path | None, typer.Option("--state")] = None,
) -> None:
    """Generate the configuration files from an approved plan (writes nothing else)."""
    _run_setup(
        ctx,
        lambda service: service.apply(service.load_plan(plan)),
        project_root=project_root,
        state=state,
    )


@setup_app.command("verify")
def setup_verify(
    ctx: typer.Context,
    plan: Annotated[Path, typer.Option("--plan")],
    project_root: Annotated[
        Path | None,
        typer.Option("--project-root"),
    ] = None,
    state: Annotated[Path | None, typer.Option("--state")] = None,
) -> None:
    """Check the applied configuration and print the remaining next steps."""
    _run_setup(
        ctx,
        lambda service: service.verify(service.load_plan(plan)),
        project_root=project_root,
        state=state,
    )


def _setup_context(ctx: typer.Context) -> SetupCommandContext:
    value = ctx.find_object(SetupCommandContext)
    if value is None:
        raise RuntimeError("setup CLI context is unavailable")
    return value


def _run_setup(
    ctx: typer.Context,
    operation: Callable[[SetupService], object],
    *,
    project_root: Path | None = None,
    state: Path | None = None,
) -> None:
    service: SetupService | None = None
    try:
        service = _selected_service(ctx, project_root, state)
        data = operation(service)
        workspace = service.load_answers().workspace or "setup"
        _emit_setup(data, workspace=workspace)
    except (KipError, PydanticValidationError, ValueError) as exc:
        workspace = (
            service.load_answers().workspace
            if service is not None
            else None
        ) or "setup"
        _emit_setup_error(exc, workspace=workspace)
        if isinstance(exc, NotFoundError):
            code = 4
        elif isinstance(
            exc,
            (ConflictError, ValidationError, PydanticValidationError, ValueError),
        ):
            code = 3
        else:
            code = 1
        raise typer.Exit(code=code) from exc


def _selected_service(
    ctx: typer.Context,
    project_root: Path | None,
    state: Path | None,
) -> SetupService:
    configured = _setup_context(ctx).service
    if project_root is None and state is None:
        return configured
    root = (
        project_root.expanduser().resolve()
        if project_root is not None
        else configured.project_root
    )
    state_path = state or configured.state_path
    if not state_path.is_absolute():
        state_path = root / state_path
    return SetupService(project_root=root, state_path=state_path)


def _emit_setup(data: object, *, workspace: str) -> None:
    envelope = Envelope(
        ok=True,
        data=_serialize(data),
        meta=EnvelopeMeta(request_id=new_id("req"), workspace=workspace),
    )
    typer.echo(envelope.model_dump_json(indent=2))


def _emit_setup_error(exc: Exception, *, workspace: str) -> None:
    code = {
        NotFoundError: "not_found",
        ConflictError: "conflict",
        ValidationError: "validation_error",
        AuthorizationError: "forbidden",
        PydanticValidationError: "validation_error",
        ValueError: "validation_error",
    }.get(type(exc), "internal_error")
    envelope = Envelope(
        ok=False,
        error=ErrorInfo(code=code, message=str(exc)),
        meta=EnvelopeMeta(request_id=new_id("req"), workspace=workspace),
    )
    typer.echo(envelope.model_dump_json(indent=2), err=True)


def _serialize(value: object) -> JsonValue:
    if isinstance(value, BaseModel):
        return cast(JsonValue, value.model_dump(mode="json"))
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"setup result is not JSON serializable: {type(value).__name__}")
