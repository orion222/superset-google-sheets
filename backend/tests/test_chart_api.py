from __future__ import annotations

from datetime import datetime, timezone
import inspect
from unittest.mock import MagicMock, patch

import pytest
from marshmallow import ValidationError as MarshmallowValidationError
from superset_core.exports.types import ChartQueryResult, GenericDataType

from michael_s_molina.sqllab_gsheets.chart_api import capabilities
from michael_s_molina.sqllab_gsheets import chart_api
from michael_s_molina.sqllab_gsheets.api import GSheetsExportAPI
from michael_s_molina.sqllab_gsheets.errors import ExportRejected
from michael_s_molina.sqllab_gsheets.google_sheets import GoogleExportError

MODULE = "michael_s_molina.sqllab_gsheets.chart_api"
FLOW = "michael_s_molina.sqllab_gsheets.export_flow"
API_MODULE = "michael_s_molina.sqllab_gsheets.api"
NOW = datetime(2026, 11, 3, 10, 5, tzinfo=timezone.utc)
PAYLOAD = {"form_data": {}}
ONE = [ChartQueryResult(["a"], [GenericDataType.NUMERIC], [{"a": 1}])]


def export_chart(payload, chart_name, email, now):
    request = chart_api.ChartExportRequest.parse(
        {"payload": payload, "chartName": chart_name}
    )
    return chart_api.export_chart(request, email, now)


class ValidationError(Exception):
    """Stands in for marshmallow's ValidationError."""


class SupersetSecurityException(Exception):
    """Stands in for the exception class Superset raises on denied access."""


class ChildSupersetSecurityException(SupersetSecurityException):
    """Stands in for a specialized security exception."""


@pytest.fixture
def m(app):
    with (
        patch(f"{MODULE}.run_query_context") as run,
        patch(f"{FLOW}.can_export_data", return_value=True) as can,
        patch(f"{FLOW}.create_spreadsheet") as create,
    ):
        run.return_value = ONE
        create.return_value = "https://docs.google.com/spreadsheets/d/f/edit"

        class Mocks:
            run_query_context = run
            can_export_data = can
            create_spreadsheet = create

        yield Mocks


def test_exports_and_titles(app, m):
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
    export_chart(PAYLOAD, None, "u@x.com", NOW)
    assert m.create_spreadsheet.call_args.args[0].startswith("Untitled chart · ")


def test_multi_query_tabs(app, m):
    m.run_query_context.return_value = ONE * 2
    export_chart({"form_data": {"slice_id": 7}}, "Sales", "u@x.com", NOW)
    assert [t.name for t in m.create_spreadsheet.call_args.args[1]] == [
        "7 - Sales",
        "7.1 - Sales",
    ]


def test_long_chart_names_are_deduplicated_after_truncation(app, m):
    m.run_query_context.return_value = ONE * 2
    long_name = "S" * 120
    export_chart(PAYLOAD, long_name, "u@x.com", NOW)
    names = [tab.name for tab in m.create_spreadsheet.call_args.args[1]]
    assert names == ["S" * 100, "S" * 98 + "~2"]


@pytest.mark.parametrize(
    ("chart_id", "name", "index", "expected"),
    [
        (7, "Sales", 0, "7 - Sales"),
        (7, "Sales", 2, "7.2 - Sales"),
        (None, "Untitled chart", 0, "Untitled chart"),
        (None, "Untitled chart", 1, "Untitled chart (1)"),
        (0, "Sales", 0, "Sales"),
    ],
)
def test_chart_tab_label(chart_id, name, index, expected):
    assert chart_api.tab_label(chart_id, name, index) == expected


def test_chart_export_request_wraps_validated_payload():
    chart_request = chart_api.ChartExportRequest.parse(
        {"payload": {"form_data": {"slice_id": 7}, "queries": []}, "chartName": "Sales"}
    )
    assert chart_request.payload["form_data"]["slice_id"] == 7
    assert chart_request.chart_name == "Sales"


def test_no_export_permission_is_403(app, m):
    m.can_export_data.return_value = False
    with pytest.raises(ExportRejected) as e:
        export_chart(PAYLOAD, "x", "u@x.com", NOW)
    assert e.value.status == 403
    m.run_query_context.assert_not_called()


def test_no_email_is_403(app, m):
    with pytest.raises(ExportRejected) as e:
        export_chart(PAYLOAD, "x", None, NOW)
    assert e.value.status == 403
    m.run_query_context.assert_not_called()


def test_access_denied_is_403(app, m):
    m.run_query_context.side_effect = SupersetSecurityException()
    with pytest.raises(ExportRejected) as e:
        export_chart(PAYLOAD, "x", "u@x.com", NOW)
    assert e.value.status == 403


def test_access_denied_subclass_is_403(app, m):
    m.run_query_context.side_effect = ChildSupersetSecurityException()
    with pytest.raises(ExportRejected) as e:
        export_chart(PAYLOAD, "x", "u@x.com", NOW)
    assert e.value.status == 403


def test_query_failure_is_generic_500(app, m):
    m.run_query_context.side_effect = RuntimeError("secret table details")
    assert _call_chart_endpoint(app) == (
        500,
        {"message": "Couldn't create the Google Sheet. Try again later."},
    )


def test_too_many_rows_is_400_before_any_google_call(app, m):
    app.config["GSHEETS_EXPORT_SYNC_MAX_ROWS"] = 1
    m.run_query_context.return_value = [
        ChartQueryResult(["a"], [GenericDataType.NUMERIC], [{"a": 1}, {"a": 2}])
    ]
    with pytest.raises(ExportRejected) as e:
        export_chart(PAYLOAD, "x", "u@x.com", NOW)
    assert (e.value.status, e.value.message) == (
        400,
        "Too large for Google Sheets (more than 1 rows). Use Export to .CSV instead.",
    )
    m.create_spreadsheet.assert_not_called()


def test_too_many_cells_is_400_before_any_google_call(app, m):
    m.run_query_context.return_value = ONE * 2
    with patch("michael_s_molina.sqllab_gsheets.google_sheets.MAX_CELLS", 3):
        with pytest.raises(ExportRejected) as e:
            export_chart(PAYLOAD, "x", "u@x.com", NOW)
    assert e.value.status == 400
    assert e.value.message.startswith("Too large for Google Sheets (4 cells,")
    m.create_spreadsheet.assert_not_called()


def test_capabilities_matches_the_export_permission(app, m):
    m.can_export_data.return_value = False
    assert capabilities(email="u@x.com") == {"can_export": False}
    assert capabilities(email=None) == {"can_export": False}
    assert capabilities(email="") == {"can_export": False}
    m.can_export_data.return_value = True
    assert capabilities(email="u@x.com") == {"can_export": True}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"data": "{not json", "content_type": "application/json"},
        {"data": "payload=x", "content_type": "application/x-www-form-urlencoded"},
        {"json": [1]},
    ],
)
def test_endpoint_malformed_body_is_400(app, kwargs):
    api = GSheetsExportAPI()
    api.response = lambda status, **kw: (status, kw)
    endpoint = inspect.unwrap(GSheetsExportAPI.export_chart_to_sheets)
    with (
        app.test_request_context(method="POST", **kwargs),
        patch(f"{API_MODULE}.g", MagicMock(user=MagicMock(email="u@x.com"))),
        patch(f"{API_MODULE}.export_chart") as export,
    ):
        assert endpoint(api) == (400, {"message": "Invalid export request."})
    export.assert_not_called()


def test_google_failure_is_generic_500(app, m):
    m.create_spreadsheet.side_effect = GoogleExportError()
    with pytest.raises(ExportRejected) as e:
        export_chart(PAYLOAD, "x", "u@x.com", NOW)
    assert (e.value.status, e.value.message) == (
        500,
        "Couldn't create the Google Sheet. Try again later.",
    )


@pytest.mark.parametrize(
    ("payload", "name"),
    [
        (None, "x"),
        ({}, "x"),
        ([], "x"),
        ("q", "x"),
        ({"form_data": []}, "x"),
        ({"form_data": "a"}, "x"),
        (PAYLOAD, 5),
        (PAYLOAD, ["x"]),
    ],
)
def test_malformed_request_is_400(app, m, payload, name):
    with pytest.raises(ExportRejected) as e:
        export_chart(payload, name, "u@x.com", NOW)
    assert (e.value.status, e.value.message) == (400, "Invalid export request.")
    m.run_query_context.assert_not_called()


def test_query_validation_errors_are_400(app, m):
    exc = MarshmallowValidationError("secret")
    m.run_query_context.side_effect = exc
    with pytest.raises(ExportRejected) as e:
        export_chart(PAYLOAD, "x", "u@x.com", NOW)
    assert (e.value.status, e.value.message) == (400, "Invalid export request.")


def test_other_exception_named_validation_error_is_generic_500(app, m):
    m.run_query_context.side_effect = ValidationError("driver details")
    assert _call_chart_endpoint(app) == (
        500,
        {"message": "Couldn't create the Google Sheet. Try again later."},
    )


def test_query_time_value_error_is_generic_500(app, m):
    m.run_query_context.side_effect = ValueError("driver failed")
    assert _call_chart_endpoint(app) == (
        500,
        {"message": "Couldn't create the Google Sheet. Try again later."},
    )


def test_chart_export_request_parser_is_available():
    assert hasattr(chart_api, "ChartExportRequest")


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"payload": None, "chartName": "Sales"},
        {"payload": {}, "chartName": "Sales"},
        {"payload": {"form_data": []}, "chartName": "Sales"},
        {"payload": {"form_data": {"slice_id": "7"}}, "chartName": "Sales"},
        {"payload": PAYLOAD, "chartName": 5},
    ],
)
def test_chart_export_request_rejects_malformed_body(body):
    with pytest.raises(ExportRejected) as e:
        chart_api.ChartExportRequest.parse(body)
    assert (e.value.status, e.value.message) == (400, "Invalid export request.")


def test_chart_export_request_rejects_non_row_result_types(app, m):
    with pytest.raises(ExportRejected) as e:
        chart_api.ChartExportRequest.parse(
            {"payload": {**PAYLOAD, "result_type": "query"}, "chartName": "Sales"}
        )
    assert (e.value.status, e.value.message) == (400, "Invalid export request.")
    m.run_query_context.assert_not_called()


def test_chart_queries_are_capped_without_mutating_the_request(app, m):
    app.config["GSHEETS_EXPORT_SYNC_MAX_ROWS"] = 3
    payload = {
        "form_data": {},
        "queries": [
            {"row_limit": 20},
            {"row_limit": 20.5},
            {"row_limit": 2},
            {},
        ],
    }
    original = {
        "form_data": {},
        "queries": [
            {"row_limit": 20},
            {"row_limit": 20.5},
            {"row_limit": 2},
            {},
        ],
    }
    export_chart(payload, "Sales", "u@x.com", NOW)
    capped = m.run_query_context.call_args.args[0]
    assert [query["row_limit"] for query in capped["queries"]] == [4, 4, 2, 4]
    assert payload == original


def test_chart_endpoint_uses_shared_response_key(app):
    api = GSheetsExportAPI()
    api.response = lambda status, **kw: (status, kw)
    endpoint = inspect.unwrap(GSheetsExportAPI.export_chart_to_sheets)
    with (
        app.test_request_context(
            method="POST", json={"payload": PAYLOAD, "chartName": "Sales"}
        ),
        patch(f"{API_MODULE}.g", MagicMock(user=MagicMock(email="u@x.com"))),
        patch(
            f"{API_MODULE}.export_chart",
            return_value="https://docs.google.com/sheets/f",
        ),
    ):
        assert endpoint(api) == (
            200,
            {"spreadsheet_url": "https://docs.google.com/sheets/f"},
        )


def test_chart_routes_share_the_sql_lab_api_registration():
    assert GSheetsExportAPI.export_chart_to_sheets._permission_name == "export"
    assert GSheetsExportAPI.chart_capabilities._permission_name == "export"
    assert not hasattr(chart_api, "ChartExportAPI")


def test_chart_google_failure_is_not_logged_twice(app, m, caplog):
    import logging

    def fail(*_args):
        logging.getLogger("michael_s_molina.sqllab_gsheets.google_sheets").error(
            "Google Sheets export failed"
        )
        raise GoogleExportError()

    m.create_spreadsheet.side_effect = fail
    with pytest.raises(ExportRejected):
        export_chart(PAYLOAD, "x", "u@x.com", NOW)
    assert (
        sum(
            record.message == "Google Sheets export failed"
            for record in caplog.records
        )
        == 1
    )


def test_tab_building_failure_is_generic_500(app, m):
    with patch(f"{MODULE}.build_tab_name", side_effect=ValueError("boom")):
        assert _call_chart_endpoint(app) == (
            500,
            {"message": "Couldn't create the Google Sheet. Try again later."},
        )
    m.create_spreadsheet.assert_not_called()


def _call_chart_endpoint(app):
    api = GSheetsExportAPI()
    api.response = lambda status, **kw: (status, kw)
    endpoint = inspect.unwrap(GSheetsExportAPI.export_chart_to_sheets)
    with (
        app.test_request_context(
            method="POST", json={"payload": PAYLOAD, "chartName": "x"}
        ),
        patch(f"{API_MODULE}.g", MagicMock(user=MagicMock(email="u@x.com"))),
    ):
        return endpoint(api)
