import pytest

from michael_s_molina.sqllab_gsheets.config import (
    export_timeout_seconds,
    GoogleSheetsConfigError,
    service_account_info,
    shared_drive_folder_id,
    sql_lab_max_rows,
    sync_max_rows,
)


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
    ("sql_max_row", "expected"),
    [
        (None, 100_000),
        (100_000, 99_999),
        (500, 499),
        (10**9, 100_000),
        (0, 100_000),
        (True, 100_000),
    ],
)
def test_sql_lab_max_rows_stays_below_sql_max_row(app, sql_max_row, expected):
    if sql_max_row is not None:
        app.config["SQL_MAX_ROW"] = sql_max_row
    assert sql_lab_max_rows() == expected
