from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from michael_s_molina.sqllab_gsheets.config import (
    export_timeout_seconds,
    service_account_info,
    shared_drive_folder_id,
    sql_lab_max_rows,
    sync_max_rows,
)
from michael_s_molina.sqllab_gsheets.errors import GoogleSheetsConfigError


def test_defaults(app):
    assert sync_max_rows() == 100_000
    assert export_timeout_seconds() == 900


def test_overrides(app):
    app.config.update(GSHEETS_EXPORT_SYNC_MAX_ROWS=10, GSHEETS_EXPORT_TIMEOUT_SECONDS=60)
    assert (sync_max_rows(), export_timeout_seconds()) == (10, 60)


@pytest.mark.parametrize(
    "key, fn",
    [
        ("GSHEETS_SHARED_DRIVE_FOLDER_ID", shared_drive_folder_id),
        ("GSHEETS_SERVICE_ACCOUNT", service_account_info),
    ],
)
def test_missing_required_key_raises(app, key, fn):
    app.config.pop(key)
    with pytest.raises(GoogleSheetsConfigError):
        fn()


@pytest.mark.parametrize(
    "key, fn",
    [
        ("GSHEETS_SHARED_DRIVE_FOLDER_ID", shared_drive_folder_id),
        ("GSHEETS_SERVICE_ACCOUNT", service_account_info),
    ],
)
def test_empty_required_key_raises(app, key, fn):
    app.config[key] = None
    with pytest.raises(GoogleSheetsConfigError):
        fn()


def test_required_values_returned(app):
    assert shared_drive_folder_id() == "folder-1"
    assert service_account_info() == {"type": "service_account"}


@pytest.mark.parametrize(
    ("key", "value", "fn"),
    [
        ("GSHEETS_SHARED_DRIVE_FOLDER_ID", 123, shared_drive_folder_id),
        ("GSHEETS_SHARED_DRIVE_FOLDER_ID", "   ", shared_drive_folder_id),
        ("GSHEETS_SERVICE_ACCOUNT", ["service_account"], service_account_info),
        ("GSHEETS_EXPORT_SYNC_MAX_ROWS", "100", sync_max_rows),
        ("GSHEETS_EXPORT_SYNC_MAX_ROWS", 0, sync_max_rows),
        ("GSHEETS_EXPORT_SYNC_MAX_ROWS", True, sync_max_rows),
        ("GSHEETS_EXPORT_TIMEOUT_SECONDS", "900", export_timeout_seconds),
        ("GSHEETS_EXPORT_TIMEOUT_SECONDS", 0, export_timeout_seconds),
        ("GSHEETS_EXPORT_TIMEOUT_SECONDS", float("nan"), export_timeout_seconds),
        ("GSHEETS_EXPORT_TIMEOUT_SECONDS", float("inf"), export_timeout_seconds),
    ],
)
def test_invalid_config_values_raise(app, key, value, fn):
    app.config[key] = value
    with pytest.raises(GoogleSheetsConfigError):
        fn()


@pytest.mark.parametrize(
    ("sql_max_row", "expected"),
    [
        (None, 100_000),
        (100_000, 99_999),
        (500, 499),
        (10**9, 100_000),
        (0, 100_000),
        (True, 100_000),
        (False, 100_000),
        (500.0, 499),
        (500.7, 499),
        (-5, 100_000),
        (0.0, 100_000),
        ("500", 100_000),
        (float("nan"), 100_000),
        (float("inf"), 100_000),
    ],
)
def test_sql_lab_max_rows_stays_below_sql_max_row(app, sql_max_row, expected):
    if sql_max_row is not None:
        app.config["SQL_MAX_ROW"] = sql_max_row
    assert sql_lab_max_rows() == expected


def test_sql_lab_query_uses_configured_row_cap(app):
    from michael_s_molina.sqllab_gsheets.api import GSheetsExportAPI
    from superset_core.queries.types import QueryStatus

    app.config.update(GSHEETS_EXPORT_SYNC_MAX_ROWS=1000, SQL_MAX_ROW=500)
    database = MagicMock()
    database.id = 7
    database.execute.return_value = SimpleNamespace(
        status=QueryStatus.SUCCESS,
        statements=[SimpleNamespace(data=pd.DataFrame({"value": [1]}))],
    )
    api = object.__new__(GSheetsExportAPI)

    api._execute_query(database, "SELECT 1", None, None)

    options = database.execute.call_args.args[1]
    assert options.limit == 499
