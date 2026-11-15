GENERIC_FAILURE = "Couldn't create the Google Sheet. Try again later."
INVALID_REQUEST = "Invalid export request."
QUERY_FAILED = "The query failed. Run it in SQL Lab to see the error."


class QueryFailed(Exception):
    """A query returned a non-success status with a driver error message."""


def is_security_exception(error: BaseException) -> bool:
    """Recognize Superset security exceptions without importing Superset itself."""
    return is_named_exception(error, "SupersetSecurityException")


class GoogleSheetsConfigError(Exception):
    """Raised when a required GSHEETS_* setting is missing or invalid."""


class ExportRejected(Exception):
    """An export refused with an HTTP status and a message safe to show the user."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def is_named_exception(
    ex: BaseException, name: str, module: str | None = None
) -> bool:
    """Match optional host exceptions by name, including their subclasses."""
    return any(
        cls.__name__ == name
        and (module is None or cls.__module__.split(".", 1)[0] == module)
        for cls in type(ex).__mro__
    )
