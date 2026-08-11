from __future__ import annotations

from pydantic import ValidationError as PydanticValidationError


class KipError(Exception):
    """Base domain error."""


class ConfigurationError(KipError):
    pass


class DependencyUnavailableError(KipError):
    pass


class NotFoundError(KipError):
    pass


class AuthorizationError(KipError):
    pass


class ConflictError(KipError):
    pass


class ValidationError(KipError):
    pass


class ParserError(KipError):
    pass


class SourceUnavailableError(KipError):
    pass


# Single source of truth for how every edge (CLI, REST, MCP) labels an
# error. Keeping it here stops the three surfaces from drifting — a
# Pydantic request-model error being reported as internal_error on one
# surface and validation_error on another is exactly the kind of drift
# this prevents.
_ERROR_CODES: dict[type[BaseException], tuple[str, int]] = {
    NotFoundError: ("not_found", 404),
    ConflictError: ("conflict", 409),
    ValidationError: ("validation_error", 422),
    AuthorizationError: ("forbidden", 403),
    ConfigurationError: ("configuration_error", 500),
    DependencyUnavailableError: ("dependency_unavailable", 503),
    SourceUnavailableError: ("source_unavailable", 503),
    ParserError: ("parser_error", 422),
}


def error_code(exc: BaseException) -> str:
    """Stable machine code for an error, shared by every edge adapter."""
    if isinstance(exc, PydanticValidationError):
        return "validation_error"
    for error_type, (code, _status) in _ERROR_CODES.items():
        if isinstance(exc, error_type):
            return code
    return "internal_error"


def http_status(exc: BaseException) -> int:
    """HTTP status for an error, shared by the REST edge."""
    if isinstance(exc, PydanticValidationError):
        return 422
    for error_type, (_code, status) in _ERROR_CODES.items():
        if isinstance(exc, error_type):
            return status
    return 500
