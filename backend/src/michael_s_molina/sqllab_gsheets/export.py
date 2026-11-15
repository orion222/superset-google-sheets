"""Shared helpers for Google Sheets export endpoints."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

from flask import Response, request
from superset_core.exports.export import can_export_data
from superset_core.rest_api.api import RestApi

from .errors import ExportRejected, GENERIC_FAILURE, INVALID_REQUEST
from .google_sheets import GoogleExportError, Tab, check_cell_limit, create_spreadsheet

logger = logging.getLogger(__name__)

EXPORT_PERMISSION_DENIED = "You don't have permission to export to Google Sheets."


def require_exporter(email: str | None) -> str:
    """Return the exporter's email or reject the export before resource lookup."""
    if not email or not can_export_data():
        raise ExportRejected(403, EXPORT_PERMISSION_DENIED)
    return email


def publish(name: str, tabs: list[Tab], email: str, now: datetime) -> str:
    """Check sheet size and publish the tabs with a timestamped title."""
    check_cell_limit(tabs)
    title = f"{name} · {now.strftime('%Y-%m-%d %H:%M')} UTC"
    try:
        return create_spreadsheet(title, tabs, email)
    except GoogleExportError as ex:
        raise ExportRejected(500, GENERIC_FAILURE) from ex


def json_export_endpoint(
    api: RestApi,
    handler: Callable[[dict[str, Any]], Response],
) -> Response:
    """Parse a JSON object and map safe export errors to endpoint responses."""
    try:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ExportRejected(400, INVALID_REQUEST)
        return handler(body)
    except ExportRejected as ex:
        return api.response(ex.status, message=ex.message)
    except Exception:
        logger.exception("Unexpected error in Google Sheets export")
        return api.response(500, message=GENERIC_FAILURE)
