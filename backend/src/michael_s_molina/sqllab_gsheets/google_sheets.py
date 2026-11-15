"""Creates and populates spreadsheets in Google Drive; the only module that talks to Google."""

from __future__ import annotations

import json
import logging
import time
from contextlib import closing
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import auth
from .chart import tab_label
from .cells import CellValue
from .config import credentials_info, export_timeout_seconds, folder_id
from .errors import GENERIC_FAILURE
from .google_http import GoogleHttp
from .tabs import MAX_CELLS, Tab, check_cell_limit

logger = logging.getLogger(__name__)

MAX_WRITE_BYTES = 2_000_000
MAX_ATTEMPTS = 5
DATE_FORMAT = {"type": "DATE_TIME", "pattern": "yyyy-mm-dd hh:mm:ss"}
# New spreadsheets start with a default tab whose ID is 0.
DEFAULT_SHEET_ID = 0
SPREADSHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
EXPORT_ID_PROPERTY = "superset_export_id"


@dataclass(frozen=True)
class CreatedFile:
    file_id: str
    owner_emails: tuple[str, ...]


class GoogleExportError(Exception):
    def __init__(self) -> None:
        super().__init__(GENERIC_FAILURE)


def _execute(request: Any, *, retry_safe: bool = False) -> Any:
    """Retry only operations whose repeated application has the same outcome."""
    return request.execute(num_retries=MAX_ATTEMPTS - 1 if retry_safe else 0)


def _is_ambiguous(error: Exception) -> bool:
    if isinstance(error, HttpError):
        status = getattr(error.resp, "status", None)
        return status is None or status >= 500 or status in {408, 429}
    return isinstance(error, OSError)


def _file_metadata(info: dict[str, Any]) -> CreatedFile:
    owners = tuple(
        owner["emailAddress"]
        for owner in info.get("owners", [])
        if owner.get("emailAddress")
    )
    return CreatedFile(file_id=info["id"], owner_emails=owners)


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


def _create_file(drive: Any, title: str, parent: str) -> CreatedFile:
    """Create once and recover a lost response by its private export marker."""
    export_id = uuid4().hex
    try:
        return _file_metadata(
            _execute(
                drive.files().create(
                    body={
                        "name": title,
                        "mimeType": SPREADSHEET_MIME_TYPE,
                        "parents": [parent],
                        "appProperties": {EXPORT_ID_PROPERTY: export_id},
                    },
                    supportsAllDrives=True,
                    fields="id,owners(emailAddress)",
                )
            )
        )
    except (HttpError, OSError) as ex:
        if not _is_ambiguous(ex):
            raise
        time.sleep(0.5)
        logger.warning(
            "Reconciling uncertain spreadsheet creation for export %s", export_id
        )
        recovered_id: str | None = None
        try:
            recovered_id = _find_created_file(drive, parent, export_id)
        except Exception:
            logger.exception("Couldn't look up spreadsheet for export %s", export_id)
        if recovered_id is not None:
            return CreatedFile(file_id=recovered_id, owner_emails=())
        # Search visibility is not proof that creation failed; never create again.
        logger.error("Spreadsheet creation unresolved; export marker %s", export_id)
        raise


def _a1_prefix(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def _size(rows: list[list[CellValue]]) -> int:
    return len(json.dumps(rows))


def _value_batches(tabs: list[Tab]) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_size = 0
    range_name = ""
    range_start = 0
    range_rows: list[list[CellValue]] = []
    range_size = 0

    def append_range() -> None:
        nonlocal current, current_size, range_rows, range_size
        if not range_rows:
            return
        if current and current_size + range_size > MAX_WRITE_BYTES:
            batches.append(current)
            current, current_size = [], 0
        current.append(
            {
                "range": f"{_a1_prefix(range_name)}!A{range_start + 1}",
                "values": range_rows,
            }
        )
        current_size += range_size
        range_rows, range_size = [], 0

    for tab in tabs:
        for row_number, row in enumerate(tab.rows):
            row_size = _size([row])
            if range_rows and range_size + row_size > MAX_WRITE_BYTES:
                append_range()
            if not range_rows:
                range_name = tab.name
                range_start = row_number
            range_rows.append(row)
            range_size += row_size
        append_range()
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


def create_spreadsheet(title: str, tabs: list[Tab], share_with: str) -> str:
    info: dict[str, Any] | None = None
    try:
        info = credentials_info()
        credentials = auth.credentials(info)
        with closing(GoogleHttp(credentials, timeout=export_timeout_seconds())) as http:
            drive = build("drive", "v3", http=http, cache_discovery=False)
            sheets = build("sheets", "v4", http=http, cache_discovery=False)

            created_file = _create_file(drive, title, folder_id())
            file_id = created_file.file_id
            try:
                sheet_ids = list(range(1, len(tabs) + 1))
                structure: list[dict[str, Any]] = [
                    {
                        "addSheet": {
                            "properties": {
                                "sheetId": sheet_id,
                                "title": tab.name,
                                "gridProperties": {"frozenRowCount": 1},
                            }
                        }
                    }
                    for tab, sheet_id in zip(tabs, sheet_ids)
                ]
                structure.extend(_format_requests(tabs, sheet_ids))
                structure.append({"deleteSheet": {"sheetId": DEFAULT_SHEET_ID}})
                _execute(
                    sheets.spreadsheets().batchUpdate(
                        spreadsheetId=file_id, body={"requests": structure}
                    )
                )

                for data in _value_batches(tabs):
                    _execute(
                        sheets.spreadsheets()
                        .values()
                        .batchUpdate(
                            spreadsheetId=file_id,
                            body={"valueInputOption": "RAW", "data": data},
                        ),
                        retry_safe=True,
                    )

                # A personal account already owns the file it created.
                if not any(
                    owner.casefold() == share_with.casefold()
                    for owner in created_file.owner_emails
                ):
                    _execute(
                        drive.permissions().create(
                            fileId=file_id,
                            body={
                                "type": "user",
                                "role": "writer",
                                "emailAddress": share_with,
                            },
                            supportsAllDrives=True,
                            sendNotificationEmail=True,
                        )
                    )
            except Exception:
                _trash(drive, file_id)
                raise
    except Exception as ex:
        logger.exception("Google Sheets export failed%s", auth.failure_hint(ex, info))
        raise GoogleExportError() from ex

    return f"https://docs.google.com/spreadsheets/d/{file_id}/edit"
