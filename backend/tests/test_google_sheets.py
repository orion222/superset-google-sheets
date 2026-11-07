from __future__ import annotations

from typing import Any
from unittest.mock import ANY

import pytest
from flask import Flask

from michael_s_molina.sqllab_gsheets.cells import CellValue
from michael_s_molina.sqllab_gsheets.errors import ExportRejected
from michael_s_molina.sqllab_gsheets.google_sheets import (
    check_cell_limit,
    create_spreadsheet,
    GoogleExportError,
    tab_label,
    Tab,
)


def test_creates_in_shared_drive_writes_formats_then_shares_last(app, google):
    url = create_spreadsheet(
        "Sales · 2026-11-03 10:00 UTC", [Tab("7 - Sales", [["a"], [1]], [])], "u@x.com"
    )
    google.drive.files().create.assert_called_with(
        body={
            "name": "Sales · 2026-11-03 10:00 UTC",
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "parents": ["folder-1"],
            "appProperties": {"superset_export_id": ANY},
        },
        supportsAllDrives=True,
        fields="id",
    )
    assert [c[0] for c in google.call_order] == [
        "files.create",
        "batchUpdate",
        "values.batchUpdate",
        "batchUpdate",
        "permissions.create",
    ]
    google.drive.permissions().create.assert_called_with(
        fileId="file-1",
        body={"type": "user", "role": "writer", "emailAddress": "u@x.com"},
        supportsAllDrives=True,
        sendNotificationEmail=True,
    )
    assert url == "https://docs.google.com/spreadsheets/d/file-1/edit"


def test_first_batch_reuses_the_default_grid_then_adds_frozen_tabs(app, google):
    create_spreadsheet("t", [Tab("a", [["h"]], []), Tab("b", [["h"]], [])], "u@x.com")
    requests = (
        google.sheets.spreadsheets()
        .batchUpdate.call_args_list[0]
        .kwargs["body"]["requests"]
    )
    assert requests[0]["updateSheetProperties"]["properties"] == {
        "sheetId": 0,
        "title": "a",
        "gridProperties": {"frozenRowCount": 0, "rowCount": 1, "columnCount": 1},
    }
    assert requests[1]["addSheet"]["properties"]["title"] == "b"
    assert len(requests) == 2


def test_tab_grids_match_the_data_cell_budget(app, google):
    create_spreadsheet("t", [Tab("wide", [["a", "b"], [1, 2], [3, 4]], [])], "u@x.com")
    requests = (
        google.sheets.spreadsheets()
        .batchUpdate.call_args_list[0]
        .kwargs["body"]["requests"]
    )
    assert requests[0]["updateSheetProperties"]["properties"]["gridProperties"] == {
        "frozenRowCount": 1,
        "rowCount": 3,
        "columnCount": 2,
    }


@pytest.mark.parametrize("after_data_tab", [False, True])
def test_header_only_temporal_tabs_keep_format_ranges_within_grid(
    app: Flask, google: Any, after_data_tab: bool
) -> None:
    """A query with no records must never format a row outside its allocated grid."""
    tabs = [Tab("No rows", [["timestamp"]], [0])]
    if after_data_tab:
        tabs.insert(0, Tab("Data", [["value"], [123]], []))
    create_spreadsheet("t", tabs, "u@x.com")

    calls = google.sheets.spreadsheets().batchUpdate.call_args_list
    grids: dict[int, dict[str, int]] = {}
    for request in calls[0].kwargs["body"]["requests"]:
        if "updateSheetProperties" in request:
            properties = request["updateSheetProperties"]["properties"]
            sheet_id = properties["sheetId"]
        else:
            properties = request["addSheet"]["properties"]
            sheet_id = 100 + len(grids) - 1
        grids[sheet_id] = properties["gridProperties"]

    format_requests = calls[1].kwargs["body"]["requests"]
    bold_headers = [
        request["repeatCell"]["range"]["sheetId"]
        for request in format_requests
        if request["repeatCell"]["cell"].get("userEnteredFormat")
        == {"textFormat": {"bold": True}}
    ]
    assert set(bold_headers) == set(grids)
    for request in format_requests:
        area = request["repeatCell"]["range"]
        grid = grids[area["sheetId"]]
        assert area.get("startRowIndex", 0) < grid["rowCount"]
        assert area.get("startColumnIndex", 0) < grid["columnCount"]


@pytest.mark.parametrize("phase", ["create", "structure", "values", "format", "share"])
def test_cancel_during_google_write_trashes_the_partial_file(app, google, phase):
    """A cancellation noticed after any phase prevents a successful return."""
    operations = {
        "create": ["files.create"],
        "structure": ["files.create", "batchUpdate"],
        "values": ["files.create", "batchUpdate", "values.batchUpdate"],
        "format": ["files.create", "batchUpdate", "values.batchUpdate", "batchUpdate"],
        "share": [
            "files.create",
            "batchUpdate",
            "values.batchUpdate",
            "batchUpdate",
            "permissions.create",
        ],
    }

    def checkpoint():
        if [c[0] for c in google.call_order] == operations[phase]:
            raise RuntimeError("cancel requested")

    with pytest.raises(GoogleExportError):
        create_spreadsheet(
            "t", [Tab("a", [["h"], [1]], [])], "u@x.com", checkpoint=checkpoint
        )
    google.drive.files().update.assert_called_once_with(
        fileId="file-1", body={"trashed": True}, supportsAllDrives=True
    )
    if phase != "share":
        google.drive.permissions().create.assert_not_called()


def test_cancel_before_creation_does_not_create_a_file(app, google):
    def checkpoint():
        raise RuntimeError("cancel requested")

    with pytest.raises(GoogleExportError):
        create_spreadsheet(
            "t", [Tab("a", [["h"]], [])], "u@x.com", checkpoint=checkpoint
        )
    google.drive.files().create.assert_not_called()


def test_base_exception_during_write_cleans_up_and_propagates(app, google):
    class AbortSignal(BaseException):
        """A non-Exception signal must not become a successful task."""

    def checkpoint():
        if any(c[0] == "values.batchUpdate" for c in google.call_order):
            raise AbortSignal()

    with pytest.raises(AbortSignal):
        create_spreadsheet(
            "t", [Tab("a", [["h"]], [])], "u@x.com", checkpoint=checkpoint
        )
    google.drive.files().update.assert_called_once()
    google.drive.permissions().create.assert_not_called()


def test_headers_are_bolded_using_returned_sheet_ids(app, google):
    create_spreadsheet("t", [Tab("a", [["h"]], []), Tab("b", [["h"]], [])], "u@x.com")
    requests = (
        google.sheets.spreadsheets()
        .batchUpdate.call_args_list[1]
        .kwargs["body"]["requests"]
    )
    bold = [r["repeatCell"] for r in requests if "textFormat" in str(r)]
    assert [r["range"]["sheetId"] for r in bold] == [0, 100]
    assert bold[0]["range"]["startRowIndex"] == 0
    assert bold[0]["range"]["endRowIndex"] == 1
    assert bold[0]["cell"]["userEnteredFormat"] == {"textFormat": {"bold": True}}


def test_write_calls_do_not_grow_with_tab_count(app, google):
    create_spreadsheet(
        "t", [Tab(f"{i} - c", [["a"], [i]], []) for i in range(40)], "u@x.com"
    )
    assert google.sheets.spreadsheets().values().batchUpdate.call_count == 1
    assert google.sheets.spreadsheets().batchUpdate.call_count == 2


def test_large_payload_splits_value_writes(app, google):
    rows: list[list[CellValue]] = [["a"]]
    row: list[CellValue] = ["x" * 1000]
    rows.extend([row] * 3000)
    big = Tab("1 - big", rows, [])
    create_spreadsheet("t", [big], "u@x.com")
    calls = google.sheets.spreadsheets().values().batchUpdate.call_args_list
    assert len(calls) == 2
    ranges = [d["range"] for c in calls for d in c.kwargs["body"]["data"]]
    assert ranges[0] == "'1 - big'!A1"
    rows_first = len(calls[0].kwargs["body"]["data"][0]["values"])
    assert ranges[1] == f"'1 - big'!A{rows_first + 1}"
    total = sum(len(d["values"]) for c in calls for d in c.kwargs["body"]["data"])
    assert total == 3001


def test_value_writes_are_raw_and_quote_sheet_names(app, google):
    create_spreadsheet("t", [Tab("it's", [["a"]], [])], "u@x.com")
    kwargs = google.sheets.spreadsheets().values().batchUpdate.call_args.kwargs
    assert kwargs["body"]["valueInputOption"] == "RAW"
    assert kwargs["body"]["data"][0]["range"] == "'it''s'!A1"


def test_retries_429_then_succeeds(app, google):
    google.fail_next("values.batchUpdate", status=429, times=2)
    create_spreadsheet("t", [Tab("1 - c", [["a"]], [])], "u@x.com")
    assert google.sleep_calls == [1, 2]


def test_non_retryable_error_fails_without_sleeping(app, google):
    google.fail_next("values.batchUpdate", status=403)
    with pytest.raises(GoogleExportError):
        create_spreadsheet("t", [Tab("1 - c", [["a"]], [])], "u@x.com")
    assert google.sleep_calls == []
    google.drive.files().update.assert_called_with(
        fileId="file-1", body={"trashed": True}, supportsAllDrives=True
    )


def test_failure_after_retries_trashes_and_never_shares(app, google):
    google.fail_next("values.batchUpdate", status=429, times=5)
    with pytest.raises(GoogleExportError) as err:
        create_spreadsheet("t", [Tab("1 - c", [["a"]], [])], "u@x.com")
    google.drive.files().update.assert_called_with(
        fileId="file-1", body={"trashed": True}, supportsAllDrives=True
    )
    google.drive.permissions().create.assert_not_called()
    assert google.sleep_calls == [1, 2, 4, 8]
    assert str(err.value) == "Couldn't create the Google Sheet. Try again later."
    assert "429" not in str(err.value)


def test_trash_failure_is_swallowed(app, google):
    google.fail_next("values.batchUpdate", status=403)
    google.fail_next("files.update", status=500, times=5)
    with pytest.raises(GoogleExportError):
        create_spreadsheet("t", [Tab("1 - c", [["a"]], [])], "u@x.com")


def test_create_failure_raises_export_error_without_trashing(app, google):
    google.fail_next("files.create", status=403)
    with pytest.raises(GoogleExportError):
        create_spreadsheet("t", [Tab("1 - c", [["a"]], [])], "u@x.com")
    google.drive.files().update.assert_not_called()


def test_date_columns_formatted(app, google):
    create_spreadsheet("t", [Tab("1 - c", [["d"], [46023.0]], [0])], "u@x.com")
    fmt = (
        google.sheets.spreadsheets()
        .batchUpdate.call_args_list[1]
        .kwargs["body"]["requests"]
    )
    assert {
        "numberFormat": {"type": "DATE_TIME", "pattern": "yyyy-mm-dd hh:mm:ss"}
    } in [
        r["repeatCell"]["cell"]["userEnteredFormat"] for r in fmt if "repeatCell" in r
    ]


@pytest.mark.parametrize(
    "cid, name, i, expected",
    [
        (7, "Sales", 0, "7 - Sales"),
        (7, "Sales", 2, "7.2 - Sales"),
        (None, "Untitled chart", 0, "Untitled chart"),
        (None, "Untitled chart", 1, "Untitled chart (1)"),
        (0, "Sales", 0, "Sales"),
    ],
)
def test_tab_label(cid, name, i, expected):
    assert tab_label(cid, name, i) == expected


def test_cell_limit_counts_header_rows_across_tabs():
    wide: list[list[CellValue]] = [[0] * 1000] * 5000
    check_cell_limit([Tab("a", wide, []), Tab("b", wide, [])])  # exactly 10,000,000
    with pytest.raises(ExportRejected) as e:
        check_cell_limit([Tab("a", wide, []), Tab("b", wide + [[0] * 1000], [])])
    assert (e.value.status, e.value.message) == (
        400,
        "Too large for Google Sheets (10,001,000 cells, limit 10,000,000). "
        "Use Export to .CSV instead.",
    )


def test_cell_limit_ignores_empty_tabs():
    check_cell_limit([Tab("a", [], [])])


USER_INFO = {
    "type": "authorized_user",
    "client_id": "c",
    "client_secret": "s",
    "refresh_token": "r",
}


def test_service_account_uses_drive_and_sheets_scopes(app, google):
    create_spreadsheet("t", [Tab("a", [["h"]], [])], "u@x.com")
    google.service_account_factory.assert_called_once_with(
        {"type": "service_account"},
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    google.user_factory.assert_not_called()


def test_authorized_user_uses_drive_file_scope_only(app, google):
    app.config["GSHEETS_CREDENTIALS"] = USER_INFO
    create_spreadsheet("t", [Tab("a", [["h"]], [])], "u@x.com")
    google.user_factory.assert_called_once_with(
        USER_INFO, scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    google.service_account_factory.assert_not_called()


def test_bad_user_credentials_fail_before_any_drive_call(app, google):
    app.config["GSHEETS_CREDENTIALS"] = {"type": "authorized_user", "client_id": "c"}
    google.user_factory.side_effect = ValueError("missing refresh_token")
    with pytest.raises(GoogleExportError):
        create_spreadsheet("t", [Tab("a", [["h"]], [])], "u@x.com")
    assert google.call_order == []


def test_owner_export_skips_share_case_insensitively(app, google):
    app.config["GSHEETS_CREDENTIALS"] = USER_INFO
    url = create_spreadsheet("t", [Tab("a", [["h"]], [])], "Owner@Gmail.com")
    google.drive.about().get.assert_called_with(fields="user(emailAddress)")
    google.drive.permissions().create.assert_not_called()
    assert url == "https://docs.google.com/spreadsheets/d/file-1/edit"


def test_teammate_export_is_shared_under_personal_account(app, google):
    app.config["GSHEETS_CREDENTIALS"] = USER_INFO
    create_spreadsheet("t", [Tab("a", [["h"]], [])], "mate@x.com")
    assert [c[0] for c in google.call_order][-2:] == ["about.get", "permissions.create"]


def test_missing_owner_email_shares_as_normal(app, google):
    app.config["GSHEETS_CREDENTIALS"] = USER_INFO
    google.about_reply = {"user": {}}
    create_spreadsheet("t", [Tab("a", [["h"]], [])], "owner@gmail.com")
    google.drive.permissions().create.assert_called_once()


def test_service_account_never_calls_about(app, google):
    create_spreadsheet("t", [Tab("a", [["h"]], [])], "owner@gmail.com")
    google.drive.about().get.assert_not_called()
    google.drive.permissions().create.assert_called_once()


def test_about_failure_trashes_and_raises(app, google):
    app.config["GSHEETS_CREDENTIALS"] = USER_INFO
    google.fail_next("about.get", status=403)
    with pytest.raises(GoogleExportError):
        create_spreadsheet("t", [Tab("a", [["h"]], [])], "owner@gmail.com")
    google.drive.files().update.assert_called_with(
        fileId="file-1", body={"trashed": True}, supportsAllDrives=True
    )


def test_refresh_error_logs_rerun_hint(app, google, caplog):
    from google.auth.exceptions import RefreshError

    app.config["GSHEETS_CREDENTIALS"] = USER_INFO
    google.drive.files().create.side_effect = RefreshError("invalid_grant")
    with pytest.raises(GoogleExportError):
        create_spreadsheet("t", [Tab("a", [["h"]], [])], "owner@gmail.com")
    assert "gsheets_personal_setup.py" in caplog.text
