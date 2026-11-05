"""Creates and populates spreadsheets in Google Drive; the only module that talks to Google."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from google.oauth2 import credentials as user_credentials, service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .cells import CellValue
from .config import credentials_info, folder_id
from .errors import ExportRejected

logger = logging.getLogger(__name__)

SERVICE_ACCOUNT_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
USER_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
MAX_WRITE_BYTES = 2_000_000
MAX_ATTEMPTS = 5
RETRYABLE_STATUSES = {429, 500, 503}
# Google's per-spreadsheet cell limit.
MAX_CELLS = 10_000_000
DATE_FORMAT = {"type": "DATE_TIME", "pattern": "yyyy-mm-dd hh:mm:ss"}
# Sheet ID Google assigns to the tab every new spreadsheet starts with.
DEFAULT_SHEET_ID = 0


@dataclass(frozen=True)
class Tab:
    name: str
    rows: list[list[CellValue]]
    temporal_columns: list[int]


class GoogleExportError(Exception):
    def __init__(self) -> None:
        super().__init__("Couldn't create the Google Sheet. Try again later.")


def tab_label(chart_id: int | None, chart_name: str, index: int) -> str:
    # Drill By sends slice_id 0, so a falsy ID means the chart has no identity.
    if not chart_id:
        return chart_name if index == 0 else f"{chart_name} ({index})"
    prefix = str(chart_id) if index == 0 else f"{chart_id}.{index}"
    return f"{prefix} - {chart_name}"


def check_cell_limit(tabs: list[Tab]) -> None:
    """Refuse tabs that would exceed Google's cell limit, before any Google call."""
    cells = sum(len(tab.rows) * len(tab.rows[0]) for tab in tabs if tab.rows)
    if cells > MAX_CELLS:
        raise ExportRejected(
            400,
            f"Too large for Google Sheets ({cells:,} cells, limit {MAX_CELLS:,}). "
            "Use Export to .CSV instead.",
        )


def _execute(request: Any) -> Any:
    for attempt in range(MAX_ATTEMPTS):
        try:
            return request.execute()
        except HttpError as ex:
            retryable = getattr(ex.resp, "status", None) in RETRYABLE_STATUSES
            if not retryable or attempt == MAX_ATTEMPTS - 1:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _a1_prefix(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def _size(rows: list[list[CellValue]]) -> int:
    return len(json.dumps(rows))


def _pieces(tabs: list[Tab]) -> list[tuple[dict[str, Any], int]]:
    """Value ranges paired with their JSON size; oversize tabs are cut into row slices."""
    pieces: list[tuple[dict[str, Any], int]] = []
    for tab in tabs:
        if not tab.rows:
            continue
        start = 0
        chunk: list[list[CellValue]] = []
        chunk_size = 0
        for row_number, row in enumerate(tab.rows):
            row_size = _size([row])
            if chunk and chunk_size + row_size > MAX_WRITE_BYTES:
                pieces.append((_range(tab.name, start, chunk), chunk_size))
                start, chunk, chunk_size = row_number, [], 0
            chunk.append(row)
            chunk_size += row_size
        pieces.append((_range(tab.name, start, chunk), chunk_size))
    return pieces


def _range(name: str, start_row: int, rows: list[list[CellValue]]) -> dict[str, Any]:
    return {"range": f"{_a1_prefix(name)}!A{start_row + 1}", "values": rows}


def _value_batches(tabs: list[Tab]) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_size = 0
    for piece, size in _pieces(tabs):
        if current and current_size + size > MAX_WRITE_BYTES:
            batches.append(current)
            current, current_size = [], 0
        current.append(piece)
        current_size += size
    if current:
        batches.append(current)
    return batches


def _format_requests(tabs: list[Tab], sheet_ids: list[int]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for tab, sheet_id in zip(tabs, sheet_ids):
        requests.append(
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            }
        )
        for column in tab.temporal_columns:
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "startColumnIndex": column,
                            "endColumnIndex": column + 1,
                        },
                        "cell": {"userEnteredFormat": {"numberFormat": DATE_FORMAT}},
                        "fields": "userEnteredFormat.numberFormat",
                    }
                }
            )
    return requests


def _trash(drive: Any, file_id: str) -> None:
    try:
        _execute(
            drive.files().update(
                fileId=file_id, body={"trashed": True}, supportsAllDrives=True
            )
        )
    except Exception:
        logger.exception("Couldn't trash partial spreadsheet %s", file_id)


def _credentials(info: dict[str, Any]) -> Any:
    if info["type"] == "authorized_user":
        return user_credentials.Credentials.from_authorized_user_info(info, scopes=USER_SCOPES)
    return service_account.Credentials.from_service_account_info(
        info, scopes=SERVICE_ACCOUNT_SCOPES
    )


def create_spreadsheet(title: str, tabs: list[Tab], share_with: str) -> str:
    file_id: str | None = None
    drive: Any = None
    try:
        info = credentials_info()
        credentials = _credentials(info)
        drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
        sheets = build("sheets", "v4", credentials=credentials, cache_discovery=False)

        file_id = _execute(
            drive.files().create(
                body={
                    "name": title,
                    "mimeType": "application/vnd.google-apps.spreadsheet",
                    "parents": [folder_id()],
                },
                supportsAllDrives=True,
                fields="id",
            )
        )["id"]

        structure = [
            {
                "addSheet": {
                    "properties": {
                        "title": tab.name,
                        "gridProperties": {"frozenRowCount": 1},
                    }
                }
            }
            for tab in tabs
        ] + [{"deleteSheet": {"sheetId": DEFAULT_SHEET_ID}}]
        reply = _execute(
            sheets.spreadsheets().batchUpdate(
                spreadsheetId=file_id, body={"requests": structure}
            )
        )
        sheet_ids = [
            reply["replies"][i]["addSheet"]["properties"]["sheetId"]
            for i in range(len(tabs))
        ]

        for data in _value_batches(tabs):
            _execute(
                sheets.spreadsheets()
                .values()
                .batchUpdate(
                    spreadsheetId=file_id,
                    body={"valueInputOption": "RAW", "data": data},
                )
            )

        _execute(
            sheets.spreadsheets().batchUpdate(
                spreadsheetId=file_id,
                body={"requests": _format_requests(tabs, sheet_ids)},
            )
        )

        _execute(
            drive.permissions().create(
                fileId=file_id,
                body={"type": "user", "role": "writer", "emailAddress": share_with},
                supportsAllDrives=True,
                sendNotificationEmail=True,
            )
        )
    except Exception as ex:
        logger.exception("Google Sheets export failed")
        if file_id is not None:
            _trash(drive, file_id)
        raise GoogleExportError() from ex

    return f"https://docs.google.com/spreadsheets/d/{file_id}/edit"
