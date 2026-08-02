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
