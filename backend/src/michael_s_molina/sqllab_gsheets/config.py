from __future__ import annotations

import math
from typing import Any, TypeVar

from flask import current_app

from .errors import GoogleSheetsConfigError

DEFAULT_SYNC_MAX_ROWS = 100_000
DEFAULT_EXPORT_TIMEOUT_SECONDS = 900

T = TypeVar("T")

def _required(key: str, expected_type: type[T]) -> T:
    value = current_app.config.get(key)
    if not value or (isinstance(value, str) and not value.strip()):
        raise GoogleSheetsConfigError(f"{key} config is not set")
    if not isinstance(value, expected_type):
        raise GoogleSheetsConfigError(
            f"{key} config must be a {expected_type.__name__}"
        )
    return value


def service_account_info() -> dict[str, Any]:
    return _required("GSHEETS_SERVICE_ACCOUNT", dict)


def shared_drive_folder_id() -> str:
    return _required("GSHEETS_SHARED_DRIVE_FOLDER_ID", str)


def sync_max_rows() -> int:
    key = "GSHEETS_EXPORT_SYNC_MAX_ROWS"
    value = current_app.config.get(key, DEFAULT_SYNC_MAX_ROWS)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GoogleSheetsConfigError(f"{key} config must be a positive integer")
    return value


def export_timeout_seconds() -> float:
    key = "GSHEETS_EXPORT_TIMEOUT_SECONDS"
    value = current_app.config.get(key, DEFAULT_EXPORT_TIMEOUT_SECONDS)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GoogleSheetsConfigError(f"{key} config must be a positive finite number")
    try:
        timeout = float(value)
    except OverflowError as ex:
        raise GoogleSheetsConfigError(
            f"{key} config must be a positive finite number"
        ) from ex
    if not math.isfinite(timeout) or timeout <= 0:
        raise GoogleSheetsConfigError(f"{key} config must be a positive finite number")
    return timeout


def sql_lab_max_rows() -> int:
    """Keep the SQL Lab row-cap import available while its logic lives with the API."""
    from .api import sql_lab_max_rows as api_sql_lab_max_rows

    return api_sql_lab_max_rows()
