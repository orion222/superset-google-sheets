from __future__ import annotations

import pytest

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


def test_first_batch_adds_frozen_tabs_then_deletes_default_sheet(app, google):
    create_spreadsheet("t", [Tab("a", [["h"]], []), Tab("b", [["h"]], [])], "u@x.com")
    requests = google.sheets.spreadsheets().batchUpdate.call_args_list[0].kwargs["body"][
        "requests"
    ]
    assert requests[0]["addSheet"]["properties"] == {
        "title": "a",
        "gridProperties": {"frozenRowCount": 1},
    }
    assert requests[1]["addSheet"]["properties"]["title"] == "b"
    assert requests[2] == {"deleteSheet": {"sheetId": 0}}


def test_headers_are_bolded_using_returned_sheet_ids(app, google):
    create_spreadsheet("t", [Tab("a", [["h"]], []), Tab("b", [["h"]], [])], "u@x.com")
    requests = google.sheets.spreadsheets().batchUpdate.call_args_list[1].kwargs["body"][
        "requests"
    ]
    bold = [r["repeatCell"] for r in requests if "textFormat" in str(r)]
    assert [r["range"]["sheetId"] for r in bold] == [100, 101]
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
    big = Tab("1 - big", [["a"]] + [["x" * 1000]] * 3000, [])
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
    fmt = google.sheets.spreadsheets().batchUpdate.call_args_list[1].kwargs["body"][
        "requests"
    ]
    assert {
        "numberFormat": {"type": "DATE_TIME", "pattern": "yyyy-mm-dd hh:mm:ss"}
    } in [r["repeatCell"]["cell"]["userEnteredFormat"] for r in fmt if "repeatCell" in r]


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
    wide = [[0] * 1000] * 5000
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
