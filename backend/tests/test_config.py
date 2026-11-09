import pytest

from michael_s_molina.sqllab_gsheets.config import (
    credentials_info,
    folder_id,
    export_timeout_seconds,
    GoogleSheetsConfigError,
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
        ("GSHEETS_FOLDER_ID", folder_id),
        ("GSHEETS_CREDENTIALS", credentials_info),
    ],
)
def test_missing_required_key_raises(app, key, fn):
    app.config.pop(key)
    with pytest.raises(GoogleSheetsConfigError):
        fn()


@pytest.mark.parametrize(
    "key, fn",
    [
        ("GSHEETS_FOLDER_ID", folder_id),
        ("GSHEETS_CREDENTIALS", credentials_info),
    ],
)
def test_empty_required_key_raises(app, key, fn):
    app.config[key] = None
    with pytest.raises(GoogleSheetsConfigError):
        fn()


def test_required_values_returned(app):
    assert folder_id() == "folder-1"
    assert credentials_info() == {"type": "service_account"}


@pytest.mark.parametrize(
    "old, new, fn",
    [
        ("GSHEETS_SERVICE_ACCOUNT", "GSHEETS_CREDENTIALS", credentials_info),
        ("GSHEETS_SHARED_DRIVE_FOLDER_ID", "GSHEETS_FOLDER_ID", folder_id),
    ],
)
def test_old_key_without_new_names_the_rename(app, old, new, fn):
    app.config[old] = app.config.pop(new)
    with pytest.raises(GoogleSheetsConfigError, match=f"{old} was renamed to {new}"):
        fn()


def test_new_key_wins_when_old_key_also_set(app):
    app.config.update(GSHEETS_SERVICE_ACCOUNT={"type": "x"}, GSHEETS_SHARED_DRIVE_FOLDER_ID="old")
    assert credentials_info() == {"type": "service_account"}
    assert folder_id() == "folder-1"


def test_authorized_user_type_accepted(app):
    app.config["GSHEETS_CREDENTIALS"] = {"type": "authorized_user", "refresh_token": "r"}
    assert credentials_info()["type"] == "authorized_user"


@pytest.mark.parametrize(
    "info", [{"client_id": "c"}, {"type": "external_account"}, {"type": None}]
)
def test_unknown_or_missing_type_lists_accepted_values(app, info):
    app.config["GSHEETS_CREDENTIALS"] = info
    with pytest.raises(GoogleSheetsConfigError, match="service_account.*authorized_user"):
        credentials_info()


def test_json_string_credentials_rejected(app):
    app.config["GSHEETS_CREDENTIALS"] = '{"type": "service_account"}'
    with pytest.raises(GoogleSheetsConfigError, match="dict"):
        credentials_info()


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
