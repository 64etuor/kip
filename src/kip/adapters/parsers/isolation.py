from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Protocol, assert_never

from pydantic import BaseModel, ConfigDict, TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from kip.adapters.parsers.process_supervisor import (
    ParserIsolationLimits,
    run_bounded_process,
)
from kip.domain.json_types import JsonObject
from kip.domain.models import ContentUnit, ExtractionRun
from kip.errors import ParserError


class ParserWorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["kip.parser-request.v1"] = "kip.parser-request.v1"
    parser_key: str
    source_path: str
    project_root: str
    parser_config: JsonObject
    artifact_id: str
    document_id: str
    acl_scopes: tuple[str, ...]


class ParserWorkerSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["kip.parser-response.v1"] = "kip.parser-response.v1"
    status: Literal["succeeded"] = "succeeded"
    extraction: ExtractionRun
    units: tuple[ContentUnit, ...]


class ParserWorkerFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["kip.parser-response.v1"] = "kip.parser-response.v1"
    status: Literal["failed"] = "failed"
    code: Literal[
        "configuration_error",
        "internal_error",
        "memory_limit",
        "parser_error",
    ]
    message: str


type ParserWorkerResponse = ParserWorkerSuccess | ParserWorkerFailure


class _ParserDelegate(Protocol):
    name: str
    version: str

    def supports(self, path: Path) -> bool: ...


class IsolatedParserAdapter:
    def __init__(
        self,
        *,
        parser_key: str,
        delegate: _ParserDelegate,
        project_root: Path,
        parser_config: JsonObject,
        limits: ParserIsolationLimits,
    ) -> None:
        self.name = delegate.name
        self.version = delegate.version
        self._parser_key = parser_key
        self._delegate = delegate
        self._project_root = project_root
        self._parser_config = parser_config
        self._limits = limits

    def supports(self, path: Path) -> bool:
        return self._delegate.supports(path)

    def parse(
        self,
        path: Path,
        *,
        artifact_id: str,
        document_id: str,
        acl_scopes: list[str],
    ) -> tuple[ExtractionRun, list[ContentUnit]]:
        request = ParserWorkerRequest(
            parser_key=self._parser_key,
            source_path=str(path),
            project_root=str(self._project_root),
            parser_config=self._parser_config,
            artifact_id=artifact_id,
            document_id=document_id,
            acl_scopes=tuple(acl_scopes),
        )
        with tempfile.TemporaryDirectory(prefix="kip-parser-") as directory:
            temp_root = Path(directory)
            request_path = temp_root / "request.json"
            response_path = temp_root / "response.json"
            diagnostic_path = temp_root / "diagnostics.log"
            request_path.write_text(request.model_dump_json(), encoding="utf-8")
            result = run_bounded_process(
                (
                    sys.executable,
                    "-m",
                    "kip.adapters.parsers.isolated_worker",
                    str(request_path),
                    str(response_path),
                    str(self._limits.cpu_seconds),
                    str(self._limits.memory_bytes),
                    str(self._limits.result_bytes),
                    str(self._limits.cpu_threads),
                    str(self._limits.nice),
                ),
                response_path=response_path,
                diagnostic_path=diagnostic_path,
                cwd=self._project_root,
                limits=self._limits,
                environment=_worker_environment(self._project_root, temp_root, self._limits),
            )
        if not result.response:
            raise ParserError(
                f"parser process exited without a valid response ({result.returncode})"
            )
        try:
            response: ParserWorkerResponse = TypeAdapter(
                ParserWorkerResponse
            ).validate_json(result.response)
        except PydanticValidationError as error:
            raise ParserError("parser process returned an invalid response") from error
        match response:
            case ParserWorkerSuccess(extraction=extraction, units=units):
                if result.returncode != 0:
                    raise ParserError(
                        f"parser process exited unsuccessfully ({result.returncode})"
                    )
                return extraction, list(units)
            case ParserWorkerFailure(message=message):
                raise ParserError(message)
            case unreachable:
                assert_never(unreachable)


def _worker_environment(
    project_root: Path,
    temp_root: Path,
    limits: ParserIsolationLimits,
) -> Mapping[str, str]:
    allowed = (
        "DYLD_LIBRARY_PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "PATH",
        "SSL_CERT_FILE",
        "TZ",
        "VIRTUAL_ENV",
    )
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    inherited_python_path = os.environ.get("PYTHONPATH")
    python_paths = [str(project_root / "src")]
    if inherited_python_path:
        python_paths.append(inherited_python_path)
    thread_count = str(limits.cpu_threads)
    environment.update(
        {
            "KORDOC_OFFLINE": "1",
            "NUMEXPR_NUM_THREADS": thread_count,
            "OMP_NUM_THREADS": thread_count,
            "OPENBLAS_NUM_THREADS": thread_count,
            "PYTHONPATH": os.pathsep.join(python_paths),
            "PYTHONUNBUFFERED": "1",
            "TMPDIR": str(temp_root),
            "VECLIB_MAXIMUM_THREADS": thread_count,
        }
    )
    for key, value in os.environ.items():
        if key.startswith("KORDOC_"):
            environment[key] = value
    return environment
