from __future__ import annotations

import math
from typing import Any

from flask import current_app

DEFAULT_SYNC_MAX_ROWS = 100_000
DEFAULT_EXPORT_TIMEOUT_SECONDS = 900


class GoogleSheetsConfigError(Exception):
    """Raised when a required GSHEETS_* Superset config key is missing or empty."""


def _required(key: str) -> Any:
    value = current_app.config.get(key)
    if not value:
        raise GoogleSheetsConfigError(f"{key} config is not set")
    return value


def service_account_info() -> dict[str, Any]:
    return _required("GSHEETS_SERVICE_ACCOUNT")


def shared_drive_folder_id() -> str:
    return _required("GSHEETS_SHARED_DRIVE_FOLDER_ID")


def sync_max_rows() -> int:
    return current_app.config.get("GSHEETS_EXPORT_SYNC_MAX_ROWS", DEFAULT_SYNC_MAX_ROWS)


def sql_lab_max_rows() -> int:
    """Most rows a SQL Lab export takes; kept below SQL_MAX_ROW so truncation shows."""
    cap = sync_max_rows()
    sql_max_row = current_app.config.get("SQL_MAX_ROW")
    if (
        isinstance(sql_max_row, (int, float))
        and not isinstance(sql_max_row, bool)
        and math.isfinite(sql_max_row)
        and int(sql_max_row) > 0
    ):
        cap = min(cap, int(sql_max_row) - 1)
    return cap


def export_timeout_seconds() -> int:
    return current_app.config.get(
        "GSHEETS_EXPORT_TIMEOUT_SECONDS", DEFAULT_EXPORT_TIMEOUT_SECONDS
    )
