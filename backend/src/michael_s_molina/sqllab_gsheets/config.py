from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, TypeVar

from flask import current_app

from .auth import CREDENTIAL_FACTORIES
from .errors import GoogleSheetsConfigError

DEFAULT_SYNC_MAX_ROWS = 100_000
DEFAULT_EXPORT_TIMEOUT_SECONDS = 900
_RENAMED = {
    "GSHEETS_CREDENTIALS": "GSHEETS_SERVICE_ACCOUNT",
    "GSHEETS_FOLDER_ID": "GSHEETS_SHARED_DRIVE_FOLDER_ID",
}

T = TypeVar("T")

def _required(key: str, expected_type: type[T]) -> T:
    value = current_app.config.get(key)
    if not value or (isinstance(value, str) and not value.strip()):
        renamed_from = _RENAMED.get(key)
        if renamed_from and current_app.config.get(renamed_from):
            raise GoogleSheetsConfigError(f"{renamed_from} was renamed to {key}")
        raise GoogleSheetsConfigError(f"{key} config is not set")
    if not isinstance(value, expected_type):
        raise GoogleSheetsConfigError(
            f"{key} config must be a {expected_type.__name__}"
        )
    return value


def credentials_info() -> dict[str, Any]:
    info = _required("GSHEETS_CREDENTIALS", object)
    if not isinstance(info, dict):
        raise GoogleSheetsConfigError(
            "GSHEETS_CREDENTIALS must be a dict, not a JSON string"
        )
    credential_type = info.get("type")
    if (
        not isinstance(credential_type, str)
        or credential_type not in CREDENTIAL_FACTORIES
    ):
        raise GoogleSheetsConfigError(
            "GSHEETS_CREDENTIALS type must be one of: "
            f"{', '.join(CREDENTIAL_FACTORIES)}"
        )
    return info


def folder_id() -> str:
    return _required("GSHEETS_FOLDER_ID", str)


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
