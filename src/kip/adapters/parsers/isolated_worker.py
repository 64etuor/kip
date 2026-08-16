from __future__ import annotations

import sys
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from kip.adapters.parsers.isolation import (
    ParserWorkerFailure,
    ParserWorkerRequest,
    ParserWorkerSuccess,
)
from kip.adapters.parsers.process_supervisor import (
    ParserIsolationLimits,
    apply_process_limits,
)
from kip.errors import ConfigurationError, ParserError
from kip.settings import Settings

_EXIT_CONFIGURATION = 50
_EXIT_INTERNAL = 70
_EXIT_MEMORY = 60
_EXIT_PARSER = 40


def _write_failure(path: Path, *, code: str, message: str) -> None:
    response = ParserWorkerFailure.model_validate(
        {
            "code": code,
            "message": message,
        }
    )
    path.write_text(response.model_dump_json(), encoding="utf-8")


def _run(request_path: Path, response_path: Path) -> int:
    request = ParserWorkerRequest.model_validate_json(
        request_path.read_text(encoding="utf-8")
    )
    settings = Settings(
        project_root=Path(request.project_root),
        config_path=Path(request.project_root) / "config/kip.toml",
        raw={"parsers": request.parser_config},
    )
    from kip.adapters.parsers.registry import raw_parser_by_key

    parser = raw_parser_by_key(settings, request.parser_key)
    extraction, units = parser.parse(
        Path(request.source_path),
        artifact_id=request.artifact_id,
        document_id=request.document_id,
        acl_scopes=list(request.acl_scopes),
    )
    response = ParserWorkerSuccess(extraction=extraction, units=tuple(units))
    response_path.write_text(response.model_dump_json(), encoding="utf-8")
    return 0


def main() -> int:
    if len(sys.argv) != 8:
        return _EXIT_CONFIGURATION
    request_path = Path(sys.argv[1])
    response_path = Path(sys.argv[2])
    try:
        limits = ParserIsolationLimits(
            cpu_seconds=int(sys.argv[3]),
            memory_bytes=int(sys.argv[4]),
            result_bytes=int(sys.argv[5]),
            cpu_threads=int(sys.argv[6]),
            nice=int(sys.argv[7]),
        )
        apply_process_limits(limits)
        return _run(request_path, response_path)
    except MemoryError:
        _write_failure(
            response_path,
            code="memory_limit",
            message="parser process exceeded its memory budget",
        )
        return _EXIT_MEMORY
    except ParserError:
        _write_failure(
            response_path,
            code="parser_error",
            message="parser rejected the input document",
        )
        return _EXIT_PARSER
    except (ConfigurationError, PydanticValidationError, ValueError):
        _write_failure(
            response_path,
            code="configuration_error",
            message="parser process configuration is invalid",
        )
        return _EXIT_CONFIGURATION
    except (OSError, RuntimeError, TypeError, KeyError, AttributeError):
        _write_failure(
            response_path,
            code="internal_error",
            message="parser process failed internally",
        )
        return _EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
