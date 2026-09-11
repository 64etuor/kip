from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError as PydanticValidationError

from kip.domain.models import Envelope, EnvelopeMeta, ErrorInfo
from kip.domain.package_archive import PackageArchiveReceipt
from kip.errors import KipError, error_code
from kip.ids import new_id
from kip.package_archive import (
    PackageArchiveBuildOptions,
    build_package_archive,
    default_package_archive_output,
    verify_package_archive,
)

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Build and verify the minimal online KIP source package ZIP",
)


@app.command("build")
def build_command(
    root: Annotated[Path, typer.Option("--root")] = Path("."),
    output: Annotated[Path | None, typer.Option("--output")] = None,
    allow_dirty: Annotated[bool, typer.Option("--allow-dirty")] = False,
    source_date_epoch: Annotated[
        int | None, typer.Option("--source-date-epoch")
    ] = None,
    repository: Annotated[str | None, typer.Option("--repository")] = None,
) -> None:
    """Build a deterministic source ZIP after applying package safety policy."""
    resolved_root = root.expanduser().resolve()
    try:
        resolved_output = output or default_package_archive_output(resolved_root)
        receipt = build_package_archive(
            PackageArchiveBuildOptions(
                root=resolved_root,
                output=resolved_output,
                allow_dirty=allow_dirty,
                source_date_epoch=source_date_epoch,
                repository=repository,
            )
        )
    except (KipError, OSError, PydanticValidationError) as error:
        _emit_error(error)
        raise typer.Exit(code=3) from error
    _emit_receipt(receipt)


@app.command("verify")
def verify_command(
    archive: Annotated[Path, typer.Argument()],
) -> None:
    """Verify paths, manifest, checksums, and content without extracting."""
    try:
        receipt = verify_package_archive(archive)
    except (KipError, OSError, PydanticValidationError) as error:
        _emit_error(error)
        raise typer.Exit(code=3) from error
    _emit_receipt(receipt)


def _emit_receipt(receipt: PackageArchiveReceipt) -> None:
    envelope = Envelope(
        ok=True,
        data=receipt.model_dump(mode="json"),
        meta=EnvelopeMeta(request_id=new_id("req"), workspace="package"),
    )
    typer.echo(envelope.model_dump_json(indent=2))


def _emit_error(error: BaseException) -> None:
    envelope = Envelope(
        ok=False,
        error=ErrorInfo(code=error_code(error), message=str(error)),
        meta=EnvelopeMeta(request_id=new_id("req"), workspace="package"),
    )
    typer.echo(envelope.model_dump_json(indent=2), err=True)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
