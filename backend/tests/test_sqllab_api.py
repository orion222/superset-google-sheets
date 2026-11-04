from __future__ import annotations

import inspect
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from superset_core.queries.types import QueryResult, QueryStatus, StatementResult

from michael_s_molina.sqllab_gsheets.api import (
    execute_query,
    export_sql,
    GSheetsExportAPI,
    SQL_ACCESS_DENIED,
)
from michael_s_molina.sqllab_gsheets.errors import ExportRejected
from michael_s_molina.sqllab_gsheets.google_sheets import GoogleExportError, Tab

MODULE = "michael_s_molina.sqllab_gsheets.api"
NOW = datetime(2026, 11, 3, 10, 5, tzinfo=timezone.utc)
DB = MagicMock(name="database")
GENERIC = "Couldn't create the Google Sheet. Try again later."


class SupersetSecurityException(Exception):
    """Stands in for the exception class Superset raises on denied access."""


@pytest.fixture
def m(app):
    sm = MagicMock(name="security_manager")
    sm.can_access.return_value = True
    with (
        patch(f"{MODULE}.execute_query") as run,
        patch(f"{MODULE}.can_export_data", return_value=True) as can,
        patch(f"{MODULE}.create_spreadsheet") as create,
        patch(f"{MODULE}.current_app", MagicMock(appbuilder=MagicMock(sm=sm))),
    ):
        run.return_value = pd.DataFrame({"a": [1]})
        create.return_value = "https://docs.google.com/spreadsheets/d/f/edit"
        DB.expose_in_sqllab = True

        class Mocks:
            execute = run
            can_export_data = can
            create_spreadsheet = create
            security_manager = sm

        yield Mocks


def test_sql_export_requires_export_permission(app, m):
    m.can_export_data.return_value = False
    with pytest.raises(ExportRejected) as e:
        export_sql(DB, "select 1", None, None, "u@x.com", NOW)
    assert e.value.status == 403
    m.execute.assert_not_called()


def test_sql_export_requires_email(app, m):
    with pytest.raises(ExportRejected) as e:
        export_sql(DB, "select 1", None, None, None, NOW)
    assert e.value.status == 403
    m.execute.assert_not_called()


def test_sql_export_requires_sqllab_execute_permission(app, m):
    m.security_manager.can_access.side_effect = lambda perm, view: not (
        perm == "can_execute_sql_query" and view == "SQLLab"
    )
    with pytest.raises(ExportRejected) as e:
        export_sql(DB, "select 1", None, None, "u@x.com", NOW)
    assert (e.value.status, e.value.message) == (403, SQL_ACCESS_DENIED)
    m.execute.assert_not_called()


def test_sql_export_requires_database_exposed_in_sqllab(app, m):
    DB.expose_in_sqllab = False
    with pytest.raises(ExportRejected) as e:
        export_sql(DB, "select 1", None, None, "u@x.com", NOW)
    assert (e.value.status, e.value.message) == (403, SQL_ACCESS_DENIED)
    m.execute.assert_not_called()


def test_sql_export_requires_table_access(app, m):
    m.security_manager.raise_for_access.side_effect = SupersetSecurityException(
        "You need access to secret_table"
    )
    with pytest.raises(ExportRejected) as e:
        export_sql(DB, "select * from secret_table", "c", "s", "u@x.com", NOW)
    assert (e.value.status, e.value.message) == (403, SQL_ACCESS_DENIED)
    assert "secret_table" not in e.value.message
    m.execute.assert_not_called()


def test_sql_export_access_check_failure_is_generic_500(app, m):
    m.security_manager.raise_for_access.side_effect = RuntimeError("password=hunter2")
    with pytest.raises(ExportRejected) as e:
        export_sql(DB, "select 1", None, None, "u@x.com", NOW)
    assert (e.value.status, e.value.message) == (500, GENERIC)
    m.execute.assert_not_called()


def test_sql_export_checks_access_to_the_sql_then_runs_it(app, m):
    export_sql(DB, "select * from t", "c", "s", "u@x.com", NOW)
    m.security_manager.can_access.assert_any_call("can_execute_sql_query", "SQLLab")
    m.security_manager.raise_for_access.assert_called_once_with(
        database=DB,
        sql="select * from t",
        catalog="c",
        schema="s",
        template_params={},
        force_dataset_match=True,
    )
    m.execute.assert_called_once_with(DB, "select * from t", "c", "s")


def test_sql_export_goes_through_shared_drive_client(app, m):
    m.execute.return_value = pd.DataFrame({"a": [1]})
    url, rows = export_sql(DB, "select 1", None, None, "u@x.com", NOW)
    title, tabs, email = m.create_spreadsheet.call_args.args
    assert tabs == [Tab("Results", [["a"], [1]], [])] and rows == 1
    assert title == "SQL Lab export · 2026-11-03 10:05 UTC"
    assert email == "u@x.com" and url.endswith("/edit")


def test_sql_export_error_text_is_generic(app, m):
    m.execute.side_effect = RuntimeError("password=hunter2")
    with pytest.raises(ExportRejected) as e:
        export_sql(DB, "select 1", None, None, "u@x.com", NOW)
    assert e.value.status == 500 and e.value.message == GENERIC
    assert "hunter2" not in e.value.message


def test_sql_export_google_failure_is_generic(app, m):
    m.create_spreadsheet.side_effect = GoogleExportError()
    with pytest.raises(ExportRejected) as e:
        export_sql(DB, "select 1", None, None, "u@x.com", NOW)
    assert e.value.status == 500 and e.value.message == GENERIC


def test_execute_query_caps_rows_one_past_the_sync_limit(app):
    app.config["GSHEETS_EXPORT_SYNC_MAX_ROWS"] = 5
    database = MagicMock(name="database")
    df = pd.DataFrame({"a": [1]})
    database.execute.return_value = QueryResult(
        QueryStatus.SUCCESS, [StatementResult("select 1", "select 1", data=df)]
    )
    assert execute_query(database, "select 1", "c", "s") is df
    options = database.execute.call_args.args[1]
    assert (options.catalog, options.schema, options.limit) == ("c", "s", 6)
    assert options.template_params == {}


def _database_returning(df):
    database = MagicMock(name="database")
    database.execute.return_value = QueryResult(
        QueryStatus.SUCCESS, [StatementResult("select 1", "select 1", data=df)]
    )
    return database


def test_sql_export_refuses_a_result_at_superset_sql_max_row(app, m):
    app.config["GSHEETS_EXPORT_SYNC_MAX_ROWS"] = 3
    app.config["SQL_MAX_ROW"] = 3
    database = _database_returning(pd.DataFrame({"a": [1, 2, 3]}))
    database.expose_in_sqllab = True
    m.execute.side_effect = execute_query
    with pytest.raises(ExportRejected) as e:
        export_sql(database, "select 1", None, None, "u@x.com", NOW)
    assert (e.value.status, e.value.message) == (
        400,
        "Too large for Google Sheets (more than 2 rows). Use Export to .CSV instead.",
    )
    assert database.execute.call_args.args[1].limit == 3
    m.create_spreadsheet.assert_not_called()


def test_sql_export_without_sql_max_row_keeps_the_sync_cap(app, m):
    app.config["GSHEETS_EXPORT_SYNC_MAX_ROWS"] = 3
    database = _database_returning(pd.DataFrame({"a": [1, 2, 3]}))
    database.expose_in_sqllab = True
    m.execute.side_effect = execute_query
    assert export_sql(database, "select 1", None, None, "u@x.com", NOW)[1] == 3
    assert database.execute.call_args.args[1].limit == 4


def test_sql_export_over_row_limit_is_400_before_any_google_call(app, m):
    app.config["GSHEETS_EXPORT_SYNC_MAX_ROWS"] = 2
    m.execute.return_value = pd.DataFrame({"a": [1, 2, 3]})
    with pytest.raises(ExportRejected) as e:
        export_sql(DB, "select 1", None, None, "u@x.com", NOW)
    assert (e.value.status, e.value.message) == (
        400,
        "Too large for Google Sheets (more than 2 rows). Use Export to .CSV instead.",
    )
    m.create_spreadsheet.assert_not_called()


def test_sql_export_at_row_limit_is_exported(app, m):
    app.config["GSHEETS_EXPORT_SYNC_MAX_ROWS"] = 2
    m.execute.return_value = pd.DataFrame({"a": [1, 2]})
    assert export_sql(DB, "select 1", None, None, "u@x.com", NOW)[1] == 2


def test_sql_export_over_cell_limit_is_400_before_any_google_call(app, m):
    m.execute.return_value = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    with patch("michael_s_molina.sqllab_gsheets.google_sheets.MAX_CELLS", 5):
        with pytest.raises(ExportRejected) as e:
            export_sql(DB, "select 1", None, None, "u@x.com", NOW)
    assert e.value.status == 400
    assert e.value.message.startswith("Too large for Google Sheets (6 cells,")
    m.create_spreadsheet.assert_not_called()


def test_endpoint_is_gated_by_export_permission():
    assert GSheetsExportAPI.class_permission_name == "GoogleSheets"
    assert GSheetsExportAPI.export_gsheets._permission_name == "export"


def _call(app, body, dao_result=DB, **export_patches):
    api = GSheetsExportAPI()
    api.response = lambda status, **kw: (status, kw)
    endpoint = inspect.unwrap(GSheetsExportAPI.export_gsheets)
    with (
        app.test_request_context(json=body),
        patch(f"{MODULE}.g", MagicMock(user=MagicMock(email="u@x.com"))),
        patch(f"{MODULE}.__", side_effect=lambda text: text),
        patch(f"{MODULE}.DatabaseDAO") as dao,
        patch(f"{MODULE}.export_sql", **export_patches),
    ):
        dao.find_one_or_none.return_value = dao_result
        return endpoint(api)


@pytest.mark.parametrize(
    "body, status",
    [
        ({"databaseId": 1}, 400),
        ({"sql": "select 1"}, 400),
        ([1], 400),
    ],
)
def test_endpoint_request_validation(app, body, status):
    assert _call(app, body, return_value=("u", 1))[0] == status


def test_endpoint_unknown_database_is_404(app):
    result = _call(app, {"sql": "select 1", "databaseId": 9}, dao_result=None)
    assert result[0] == 404


def test_endpoint_success(app):
    body = {"sql": "select 1", "databaseId": 1}
    assert _call(app, body, return_value=("u", 3)) == (
        200,
        {"spreadsheet_url": "u", "row_count": 3},
    )


def test_endpoint_rejection_keeps_status_and_message(app):
    body = {"sql": "select 1", "databaseId": 1}
    result = _call(app, body, side_effect=ExportRejected(403, "nope"))
    assert result == (403, {"message": "nope"})


def test_endpoint_unexpected_error_is_generic(app):
    body = {"sql": "select 1", "databaseId": 1}
    result = _call(app, body, side_effect=RuntimeError("secret"))
    assert result == (500, {"message": GENERIC})
