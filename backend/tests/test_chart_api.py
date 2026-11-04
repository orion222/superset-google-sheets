from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from superset_core.exports.types import ChartQueryResult, GenericDataType

from michael_s_molina.sqllab_gsheets.chart_api import capabilities, export_chart
from michael_s_molina.sqllab_gsheets.errors import ExportRejected
from michael_s_molina.sqllab_gsheets.google_sheets import GoogleExportError

MODULE = "michael_s_molina.sqllab_gsheets.chart_api"
NOW = datetime(2026, 11, 3, 10, 5, tzinfo=timezone.utc)
ONE = [ChartQueryResult(["a"], [GenericDataType.NUMERIC], [{"a": 1}])]


class SupersetSecurityException(Exception):
    """Stands in for the exception class Superset raises on denied access."""


@pytest.fixture
def m(app):
    with (
        patch(f"{MODULE}.run_query_context") as run,
        patch(f"{MODULE}.can_export_data", return_value=True) as can,
        patch(f"{MODULE}.create_spreadsheet") as create,
        patch(
            f"{MODULE}.build_tab_name", side_effect=lambda raw, used, n: raw[:n]
        ),
    ):
        run.return_value = ONE
        create.return_value = "https://docs.google.com/spreadsheets/d/f/edit"

        class Mocks:
            run_query_context = run
            can_export_data = can
            create_spreadsheet = create

        yield Mocks


def test_exports_and_titles(app, m):
    m.run_query_context.return_value = ONE
    m.create_spreadsheet.return_value = "https://docs.google.com/spreadsheets/d/f/edit"
    url = export_chart({"form_data": {"slice_id": 7}}, "Sales", "u@x.com", NOW)
    title, tabs, email = m.create_spreadsheet.call_args.args
    assert title == "Sales · 2026-11-03 10:05 UTC" and email == "u@x.com"
    assert [t.name for t in tabs] == ["7 - Sales"] and url.endswith("/edit")


def test_drill_by_slice_id_zero_has_no_id_prefix(app, m):
    export_chart({"form_data": {"slice_id": 0}}, "Sales", "u@x.com", NOW)
    assert [t.name for t in m.create_spreadsheet.call_args.args[1]] == ["Sales"]


def test_missing_and_null_slice_id_have_no_id_prefix(app, m):
    export_chart({"form_data": {"slice_id": None}}, "Sales", "u@x.com", NOW)
    assert [t.name for t in m.create_spreadsheet.call_args.args[1]] == ["Sales"]
    export_chart({"form_data": {}}, "Sales", "u@x.com", NOW)
    assert [t.name for t in m.create_spreadsheet.call_args.args[1]] == ["Sales"]


def test_untitled_chart(app, m):
    export_chart({}, None, "u@x.com", NOW)
    assert m.create_spreadsheet.call_args.args[0].startswith("Untitled chart · ")


def test_multi_query_tabs(app, m):
    m.run_query_context.return_value = ONE * 2
    export_chart({"form_data": {"slice_id": 7}}, "Sales", "u@x.com", NOW)
    assert [t.name for t in m.create_spreadsheet.call_args.args[1]] == [
        "7 - Sales",
        "7.1 - Sales",
    ]


def test_no_export_permission_is_403(app, m):
    m.can_export_data.return_value = False
    with pytest.raises(ExportRejected) as e:
        export_chart({}, "x", "u@x.com", NOW)
    assert e.value.status == 403
    m.run_query_context.assert_not_called()


def test_no_email_is_403(app, m):
    with pytest.raises(ExportRejected) as e:
        export_chart({}, "x", None, NOW)
    assert e.value.status == 403


def test_access_denied_is_403(app, m):
    m.run_query_context.side_effect = SupersetSecurityException()
    with pytest.raises(ExportRejected) as e:
        export_chart({}, "x", "u@x.com", NOW)
    assert e.value.status == 403


def test_query_failure_is_generic_500(app, m):
    m.run_query_context.side_effect = RuntimeError("secret table details")
    with pytest.raises(ExportRejected) as e:
        export_chart({}, "x", "u@x.com", NOW)
    assert e.value.status == 500
    assert "secret" not in e.value.message


def test_too_many_rows_is_400_before_any_google_call(app, m):
    app.config["GSHEETS_EXPORT_SYNC_MAX_ROWS"] = 1
    m.run_query_context.return_value = [
        ChartQueryResult(["a"], [GenericDataType.NUMERIC], [{"a": 1}, {"a": 2}])
    ]
    with pytest.raises(ExportRejected) as e:
        export_chart({}, "x", "u@x.com", NOW)
    assert (e.value.status, e.value.message) == (
        400,
        "Too large for Google Sheets (2 rows, limit 1). Use Export to .CSV instead.",
    )
    m.create_spreadsheet.assert_not_called()


def test_capabilities_reflects_export_permission(app, m):
    m.can_export_data.return_value = False
    assert capabilities(email="u@x.com") == {"can_export": False}
    m.can_export_data.return_value = True
    assert capabilities(email=None) == {"can_export": False}
    assert capabilities(email="u@x.com") == {"can_export": True}


def test_google_failure_is_generic_500(app, m):
    m.create_spreadsheet.side_effect = GoogleExportError()
    with pytest.raises(ExportRejected) as e:
        export_chart({}, "x", "u@x.com", NOW)
    assert (e.value.status, e.value.message) == (
        500,
        "Couldn't create the Google Sheet. Try again later.",
    )
