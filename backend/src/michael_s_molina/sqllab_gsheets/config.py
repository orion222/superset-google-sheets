from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from flask import current_app

DEFAULT_SYNC_MAX_ROWS = 100_000
DEFAULT_EXPORT_TIMEOUT_SECONDS = 900
CREDENTIAL_TYPES = ("service_account", "authorized_user")


class GoogleSheetsConfigError(Exception):
    """Raised when a GSHEETS_* Superset config key is missing, empty, or invalid."""


def _required(key: str, renamed_from: str | None = None) -> Any:
    value = current_app.config.get(key)
    if not value:
        if renamed_from and current_app.config.get(renamed_from):
            raise GoogleSheetsConfigError(f"{renamed_from} was renamed to {key}")
        raise GoogleSheetsConfigError(f"{key} config is not set")
    return value


def credentials_info() -> dict[str, Any]:
    info = _required("GSHEETS_CREDENTIALS", renamed_from="GSHEETS_SERVICE_ACCOUNT")
    if not isinstance(info, dict):
        raise GoogleSheetsConfigError(
            "GSHEETS_CREDENTIALS must be a dict, not a JSON string"
        )
    if info.get("type") not in CREDENTIAL_TYPES:
        raise GoogleSheetsConfigError(
            f"GSHEETS_CREDENTIALS type must be one of: {', '.join(CREDENTIAL_TYPES)}"
        )
    return info


def folder_id() -> str:
    return _required("GSHEETS_FOLDER_ID", renamed_from="GSHEETS_SHARED_DRIVE_FOLDER_ID")


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
    """Return a positive worker timeout, rejecting invalid deployment settings."""
    value = current_app.config.get(
        "GSHEETS_EXPORT_TIMEOUT_SECONDS", DEFAULT_EXPORT_TIMEOUT_SECONDS
    )
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise GoogleSheetsConfigError("GSHEETS_EXPORT_TIMEOUT_SECONDS must be positive")
    return value


def background_tasks_enabled() -> bool:
    """Resolve host task flags through its public configuration callbacks."""
    flags = deepcopy(
        {
            **current_app.config.get("DEFAULT_FEATURE_FLAGS", {}),
            **current_app.config.get("FEATURE_FLAGS", {}),
        }
    )
    if flags.get("GLOBAL_ASYNC_QUERIES"):
        flags["GLOBAL_TASK_FRAMEWORK"] = True
    resolve = current_app.config.get("IS_FEATURE_ENABLED_FUNC")
    if callable(resolve):
        return bool(
            resolve("GLOBAL_TASK_FRAMEWORK", flags.get("GLOBAL_TASK_FRAMEWORK", False))
            or resolve("GLOBAL_ASYNC_QUERIES", flags.get("GLOBAL_ASYNC_QUERIES", False))
        )
    get_flags = current_app.config.get("GET_FEATURE_FLAGS_FUNC")
    if callable(get_flags):
        flags = get_flags(flags)
    return bool(flags.get("GLOBAL_TASK_FRAMEWORK") or flags.get("GLOBAL_ASYNC_QUERIES"))
