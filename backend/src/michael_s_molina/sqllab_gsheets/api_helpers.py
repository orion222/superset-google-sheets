"""Common HTTP error responses for Google Sheets export endpoints."""

import logging
from typing import Any

from .errors import ExportRejected, GENERIC_FAILURE

logger = logging.getLogger(__name__)


def export_error_response(api: Any, ex: Exception) -> Any:
    if isinstance(ex, ExportRejected):
        return api.response(ex.status, message=ex.message)
    logger.exception("Unexpected error in Google Sheets export")
    return api.response(500, message=GENERIC_FAILURE)
