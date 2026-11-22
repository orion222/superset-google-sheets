"""Creates and populates spreadsheets in Google Drive; the only module that talks to Google."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
from .tabs import MAX_CELLS, Tab, check_cell_limit, grid_dimensions

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
        if len(tab.rows) <= 1:
            continue
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


def _structure_requests(tabs: list[Tab]) -> tuple[list[dict[str, Any]], list[int]]:
    """Size each tab's grid to its data and assign sheet IDs before the first call."""
    sheet_ids = list(range(len(tabs)))
    structure: list[dict[str, Any]] = []
    for tab, sheet_id in zip(tabs, sheet_ids):
        row_count, column_count = grid_dimensions(tab.rows)
        properties = {
            "sheetId": sheet_id,
            "title": tab.name,
            "gridProperties": {
                "frozenRowCount": int(len(tab.rows) > 1),
                "rowCount": row_count,
                "columnCount": column_count,
            },
        }
        if sheet_id == DEFAULT_SHEET_ID:
            # Reuse the initial grid so it never consumes extra cell budget
            # or collides with a requested tab named Sheet1.
            structure.append(
                {
                    "updateSheetProperties": {
                        "properties": properties,
                        "fields": "title,gridProperties",
                    }
                }
            )
        else:
            structure.append({"addSheet": {"properties": properties}})
    structure.extend(_format_requests(tabs, sheet_ids))
    return structure, sheet_ids


def _noop() -> None:
    """Default checkpoint for synchronous exports."""


def create_spreadsheet(
    title: str,
    tabs: list[Tab],
    share_with: str,
    *,
    checkpoint: Callable[[], None] = _noop,
) -> str:
    """Write and share a sheet, cleaning up failures and cancellation signals.

    Exceptions raised by ``checkpoint`` propagate unchanged; Google failures
    become ``GoogleExportError``.
    """
    info: dict[str, Any] | None = None

    @contextmanager
    def google_call() -> Iterator[None]:
        try:
            yield
        except Exception as ex:
            logger.exception(
                "Google Sheets export failed%s", auth.failure_hint(ex, info)
            )
            raise GoogleExportError() from ex

    checkpoint()
    with google_call():
        info = credentials_info()
        http = GoogleHttp(auth.credentials(info), timeout=export_timeout_seconds())
    try:
        with google_call():
            drive = build("drive", "v3", http=http, cache_discovery=False)
            sheets = build("sheets", "v4", http=http, cache_discovery=False)
            created_file = _create_file(drive, title, folder_id())
        file_id = created_file.file_id
        try:
            checkpoint()
            structure, _ = _structure_requests(tabs)
            with google_call():
                _execute(
                    sheets.spreadsheets().batchUpdate(
                        spreadsheetId=file_id, body={"requests": structure}
                    )
                )

            for data in _value_batches(tabs):
                checkpoint()
                with google_call():
                    _execute(
                        sheets.spreadsheets()
                        .values()
                        .batchUpdate(
                            spreadsheetId=file_id,
                            body={"valueInputOption": "RAW", "data": data},
                        ),
                        retry_safe=True,
                    )

            # Google may send a share email as part of this call. Once sharing
            # succeeds, finish even if the task has received a cancellation.
            checkpoint()
            # A personal account already owns the file it created.
            if not any(
                owner.casefold() == share_with.casefold()
                for owner in created_file.owner_emails
            ):
                with google_call():
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
        except BaseException:
            _trash(drive, file_id)
            raise
    finally:
        try:
            http.close()
        except Exception:
            logger.warning("Couldn't close the Google API session", exc_info=True)

    return f"https://docs.google.com/spreadsheets/d/{file_id}/edit"
