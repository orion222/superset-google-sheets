GENERIC_FAILURE = "Couldn't create the Google Sheet. Try again later."
INVALID_REQUEST = "Invalid export request."
QUERY_FAILED = "The query failed. Run it in SQL Lab to see the error."


class QueryFailed(Exception):
    """A query returned a non-success status with a driver error message."""


def is_security_exception(error: BaseException) -> bool:
    """Recognize Superset security exceptions without importing Superset itself."""
    return any(
        cls.__name__ == "SupersetSecurityException" for cls in type(error).__mro__
    )


class ExportRejected(Exception):
    """An export refused with an HTTP status and a message safe to show the user."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
