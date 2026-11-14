GENERIC_FAILURE = "Couldn't create the Google Sheet. Try again later."
INVALID_REQUEST = "Invalid export request."


def is_named_exception(error: BaseException, name: str) -> bool:
    """Recognize host errors whose classes aren't in the public Superset SDK."""
    return type(error).__name__ == name


class ExportRejected(Exception):
    """An export refused with an HTTP status and a message safe to show the user."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
