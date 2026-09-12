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


# Envelope warnings an error carries to the edge. `error_code` says what
# failed; these say what the caller should also know about the failed call
# (for example that a search, not the request shape, is what broke). Kept
# here with the other shared error vocabulary so CLI, REST and MCP cannot
# drift on it.
_ENVELOPE_WARNINGS_ATTRIBUTE = "kip_envelope_warnings"


def mark_envelope_warning(exc: BaseException, warning: str) -> None:
    """Attach an envelope warning to an in-flight error, once."""
    warnings = list(getattr(exc, _ENVELOPE_WARNINGS_ATTRIBUTE, ()))
    if warning not in warnings:
        warnings.append(warning)
    setattr(exc, _ENVELOPE_WARNINGS_ATTRIBUTE, tuple(warnings))


def envelope_warnings(exc: BaseException) -> list[str]:
    """Envelope warnings recorded on an error, for `meta.warnings`."""
    warnings = getattr(exc, _ENVELOPE_WARNINGS_ATTRIBUTE, ())
    return [str(item) for item in warnings]


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
