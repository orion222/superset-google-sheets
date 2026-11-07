import pytest
from flask import Flask
from unittest.mock import MagicMock

from michael_s_molina.sqllab_gsheets.config import (
    background_tasks_enabled,
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
    app.config.update(
        GSHEETS_EXPORT_SYNC_MAX_ROWS=10, GSHEETS_EXPORT_TIMEOUT_SECONDS=60
    )
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
    app.config.update(
        GSHEETS_SERVICE_ACCOUNT={"type": "x"}, GSHEETS_SHARED_DRIVE_FOLDER_ID="old"
    )
    assert credentials_info() == {"type": "service_account"}
    assert folder_id() == "folder-1"


def test_authorized_user_type_accepted(app):
    app.config["GSHEETS_CREDENTIALS"] = {
        "type": "authorized_user",
        "refresh_token": "r",
    }
    assert credentials_info()["type"] == "authorized_user"


@pytest.mark.parametrize(
    "info", [{"client_id": "c"}, {"type": "external_account"}, {"type": None}]
)
def test_unknown_or_missing_type_lists_accepted_values(app, info):
    app.config["GSHEETS_CREDENTIALS"] = info
    with pytest.raises(
        GoogleSheetsConfigError, match="service_account.*authorized_user"
    ):
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


@pytest.mark.parametrize(
    ("defaults", "overrides", "expected"),
    [
        ({}, {}, False),
        ({"GLOBAL_TASK_FRAMEWORK": True}, {}, True),
        ({"GLOBAL_TASK_FRAMEWORK": True}, {"GLOBAL_TASK_FRAMEWORK": False}, False),
        ({}, {"GLOBAL_TASK_FRAMEWORK": True}, True),
        ({"GLOBAL_ASYNC_QUERIES": True}, {"GLOBAL_TASK_FRAMEWORK": False}, True),
        ({}, {"GLOBAL_ASYNC_QUERIES": True}, True),
    ],
)
def test_background_tasks_resolve_static_defaults_and_derived_flags(
    app: Flask, defaults: dict[str, bool], overrides: dict[str, bool], expected: bool
) -> None:
    """Task availability follows host defaults, overrides, and the GAQ dependency."""
    app.config.update(DEFAULT_FEATURE_FLAGS=defaults, FEATURE_FLAGS=overrides)
    assert background_tasks_enabled() is expected


@pytest.mark.parametrize(
    ("gtf_enabled", "gaq_enabled", "expected"),
    [(True, False, True), (False, True, True), (False, False, False)],
)
def test_background_tasks_honor_single_flag_callback_precedence(
    app: Flask, gtf_enabled: bool, gaq_enabled: bool, expected: bool
) -> None:
    """The single-flag callback can enable or disable static configuration."""
    resolved = {
        "GLOBAL_TASK_FRAMEWORK": gtf_enabled,
        "GLOBAL_ASYNC_QUERIES": gaq_enabled,
    }
    resolve = MagicMock(side_effect=lambda name, default: resolved[name])
    get_flags = MagicMock(side_effect=AssertionError("wrong callback precedence"))
    app.config.update(
        DEFAULT_FEATURE_FLAGS={"GLOBAL_TASK_FRAMEWORK": True},
        IS_FEATURE_ENABLED_FUNC=resolve,
        GET_FEATURE_FLAGS_FUNC=get_flags,
    )
    assert background_tasks_enabled() is expected
    resolve.assert_any_call("GLOBAL_TASK_FRAMEWORK", True)
    get_flags.assert_not_called()


def test_single_flag_callback_receives_the_static_gaq_derived_default(
    app: Flask,
) -> None:
    """The host derives GTF before supplying defaults to its dynamic callback."""
    resolve = MagicMock(side_effect=lambda name, default: default)
    app.config.update(
        DEFAULT_FEATURE_FLAGS={
            "GLOBAL_TASK_FRAMEWORK": False,
            "GLOBAL_ASYNC_QUERIES": True,
        },
        IS_FEATURE_ENABLED_FUNC=resolve,
    )
    assert background_tasks_enabled() is True
    resolve.assert_any_call("GLOBAL_TASK_FRAMEWORK", True)


@pytest.mark.parametrize(
    ("gtf_enabled", "gaq_enabled", "expected"),
    [(True, False, True), (False, True, True), (False, False, False)],
)
def test_background_tasks_use_copied_full_map_callback(
    app: Flask, gtf_enabled: bool, gaq_enabled: bool, expected: bool
) -> None:
    """The map callback sees all flags and cannot mutate the deployment defaults."""
    defaults = {"GLOBAL_TASK_FRAMEWORK": True, "OTHER_FEATURE": True}

    def override(flags: dict[str, bool]) -> dict[str, bool]:
        """Simulate a rollout decision that depends on another configured flag."""
        assert flags["OTHER_FEATURE"] is True
        flags["OTHER_FEATURE"] = False
        flags["GLOBAL_TASK_FRAMEWORK"] = gtf_enabled
        flags["GLOBAL_ASYNC_QUERIES"] = gaq_enabled
        return flags

    app.config.update(DEFAULT_FEATURE_FLAGS=defaults, GET_FEATURE_FLAGS_FUNC=override)
    assert background_tasks_enabled() is expected
    assert defaults == {"GLOBAL_TASK_FRAMEWORK": True, "OTHER_FEATURE": True}


@pytest.mark.parametrize(
    "value", [None, True, False, 0, -1, 1.5, "900", float("nan"), float("inf")]
)
def test_export_timeout_rejects_non_positive_or_non_integer_settings(
    app: Flask, value: object
) -> None:
    """Invalid deployment timeouts fail before a background task is submitted."""
    app.config["GSHEETS_EXPORT_TIMEOUT_SECONDS"] = value
    with pytest.raises(GoogleSheetsConfigError, match="GSHEETS_EXPORT_TIMEOUT_SECONDS"):
        export_timeout_seconds()
