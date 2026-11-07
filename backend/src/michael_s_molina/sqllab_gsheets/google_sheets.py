"""Creates and populates spreadsheets in Google Drive; the only module that talks to Google."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from google.auth.exceptions import RefreshError, TransportError
from google.oauth2 import credentials as user_credentials, service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from httplib2 import HttpLib2Error

from .cells import CellValue
from .config import credentials_info, folder_id
from .errors import ExportRejected
from .google_http import GoogleHttp

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
SPREADSHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
EXPORT_ID_PROPERTY = "superset_export_id"


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


def _execute(request: Any, *, retry_safe: bool = False) -> Any:
    """Retry only operations whose repeated application has the same outcome."""
    attempts = MAX_ATTEMPTS if retry_safe else 1
    for attempt in range(attempts):
        try:
            return request.execute()
        except HttpError as ex:
            retryable = getattr(ex.resp, "status", None) in RETRYABLE_STATUSES
            if not retryable or attempt == attempts - 1:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _find_created_file(drive: Any, parent: str, export_id: str) -> str | None:
    """Recover one marked file; an incomplete or ambiguous search cannot prove success."""
    parent_info = _execute(
        drive.files().get(fileId=parent, fields="driveId", supportsAllDrives=True),
        retry_safe=True,
    )
    drive_id = parent_info.get("driveId")
    corpus = (
        {"corpora": "drive", "driveId": drive_id} if drive_id else {"corpora": "user"}
    )
    escaped_parent = parent.replace("\\", "\\\\").replace("'", "\\'")
    query = (
        f"'{escaped_parent}' in parents and trashed = false and "
        f"mimeType = '{SPREADSHEET_MIME_TYPE}' and "
        f"appProperties has {{ key='{EXPORT_ID_PROPERTY}' and value='{export_id}' }}"
    )
    matches: set[str] = set()
    page_token: str | None = None
    while True:
        reply = _execute(
            drive.files().list(
                q=query,
                fields="files(id),nextPageToken,incompleteSearch",
                spaces="drive",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                pageToken=page_token,
                **corpus,
            ),
            retry_safe=True,
        )
        if reply.get("incompleteSearch"):
            return None
        matches.update(file["id"] for file in reply.get("files", []))
        if len(matches) > 1:
            return None
        page_token = reply.get("nextPageToken")
        if not page_token:
            return next(iter(matches), None)


def _create_file(drive: Any, title: str, parent: str) -> str:
    """Create once and recover a lost response by its private export marker."""
    export_id = uuid4().hex
    try:
        return _execute(
            drive.files().create(
                body={
                    "name": title,
                    "mimeType": SPREADSHEET_MIME_TYPE,
                    "parents": [parent],
                    "appProperties": {EXPORT_ID_PROPERTY: export_id},
                },
                supportsAllDrives=True,
                fields="id",
            )
        )["id"]
    except (HttpError, OSError, HttpLib2Error, TransportError) as ex:
        if (
            isinstance(ex, HttpError)
            and ex.resp.status < 500
            and ex.resp.status not in {408, 429}
        ):
            raise
        logger.warning(
            "Reconciling uncertain spreadsheet creation for export %s", export_id
        )
        recovered_id: str | None = None
        try:
            recovered_id = _find_created_file(drive, parent, export_id)
        except Exception:
            logger.exception("Couldn't look up spreadsheet for export %s", export_id)
        if recovered_id is not None:
            return recovered_id
        # Search visibility is not proof that creation failed; never create again.
        logger.error("Spreadsheet creation unresolved; export marker %s", export_id)
        raise


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
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                    },
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
            ),
            retry_safe=True,
        )
    except Exception:
        logger.exception("Couldn't trash partial spreadsheet %s", file_id)


def _credentials(info: dict[str, Any]) -> Any:
    if info["type"] == "authorized_user":
        return user_credentials.Credentials.from_authorized_user_info(
            info, scopes=USER_SCOPES
        )
    return service_account.Credentials.from_service_account_info(
        info, scopes=SERVICE_ACCOUNT_SCOPES
    )


def _owner_email(drive: Any) -> str | None:
    reply = _execute(drive.about().get(fields="user(emailAddress)"))
    return reply.get("user", {}).get("emailAddress")


def create_spreadsheet(
    title: str,
    tabs: list[Tab],
    share_with: str,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> str:
    """Write and share a sheet, cleaning up failures and cancellation signals."""

    def check() -> None:
        if checkpoint is not None:
            checkpoint()

    file_id: str | None = None
    drive: Any = None
    http: GoogleHttp | None = None
    try:
        check()
        info = credentials_info()
        credentials = _credentials(info)
        http = GoogleHttp(credentials)
        drive = build("drive", "v3", http=http, cache_discovery=False)
        sheets = build("sheets", "v4", http=http, cache_discovery=False)

        file_id = _create_file(drive, title, folder_id())
        check()

        structure: list[dict[str, Any]] = []
        for index, tab in enumerate(tabs):
            properties = {
                "title": tab.name,
                "gridProperties": {
                    "frozenRowCount": int(len(tab.rows) > 1),
                    "rowCount": max(1, len(tab.rows)),
                    "columnCount": max(1, max(map(len, tab.rows), default=0)),
                },
            }
            if index == 0:
                # Reuse the initial grid so it never consumes extra cell budget
                # or collides with a requested tab named Sheet1.
                structure.append(
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": DEFAULT_SHEET_ID, **properties},
                            "fields": "title,gridProperties",
                        }
                    }
                )
            else:
                structure.append({"addSheet": {"properties": properties}})
        reply = _execute(
            sheets.spreadsheets().batchUpdate(
                spreadsheetId=file_id, body={"requests": structure}
            )
        )
        sheet_ids = [
            DEFAULT_SHEET_ID,
            *[
                reply["replies"][i]["addSheet"]["properties"]["sheetId"]
                for i in range(1, len(tabs))
            ],
        ]
        check()

        for data in _value_batches(tabs):
            check()
            _execute(
                sheets.spreadsheets()
                .values()
                .batchUpdate(
                    spreadsheetId=file_id,
                    body={"valueInputOption": "RAW", "data": data},
                ),
                retry_safe=True,
            )
            check()

        _execute(
            sheets.spreadsheets().batchUpdate(
                spreadsheetId=file_id,
                body={"requests": _format_requests(tabs, sheet_ids)},
            ),
            retry_safe=True,
        )
        check()

        owner = _owner_email(drive) if info["type"] == "authorized_user" else None
        check()
        if not (owner and owner.casefold() == share_with.casefold()):
            _execute(
                drive.permissions().create(
                    fileId=file_id,
                    body={"type": "user", "role": "writer", "emailAddress": share_with},
                    supportsAllDrives=True,
                    sendNotificationEmail=True,
                )
            )
        check()
    except BaseException as ex:
        logger.exception("Google Sheets export failed")
        if isinstance(ex, RefreshError):
            logger.error(
                "Google rejected the GSHEETS_CREDENTIALS token; for a personal account, "
                "re-run scripts/gsheets_personal_setup.py"
            )
        if file_id is not None:
            _trash(drive, file_id)
        if not isinstance(ex, Exception):
            raise
        raise GoogleExportError() from ex
    finally:
        if http is not None:
            http.close()

    return f"https://docs.google.com/spreadsheets/d/{file_id}/edit"
