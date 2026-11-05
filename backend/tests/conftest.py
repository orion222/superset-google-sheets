from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httplib2
import pytest
import superset_core.rest_api.decorators as _decorators
from flask import Flask
from googleapiclient.errors import HttpError


# Outside a running Superset the real decorator raises NotImplementedError; this must
# be in place before any sqllab_gsheets module is imported.
def _passthrough_api(**_kwargs):
    return lambda cls: cls


_decorators.api = _passthrough_api


@pytest.fixture
def app():
    flask_app = Flask(__name__)
    flask_app.config.update(
        GSHEETS_CREDENTIALS={"type": "service_account"},
        GSHEETS_FOLDER_ID="folder-1",
    )
    with flask_app.app_context():
        yield flask_app


class FakeGoogle:
    """Recording stand-ins for the Drive and Sheets clients."""

    def __init__(self) -> None:
        self.drive = MagicMock(name="drive")
        self.sheets = MagicMock(name="sheets")
        self.call_order: list[tuple[str]] = []
        self.sleep_calls: list[float] = []
        self.service_account_factory = MagicMock(name="service_account_factory")
        self.user_factory = MagicMock(name="user_factory")
        self.about_reply: dict[str, Any] = {"user": {"emailAddress": "owner@gmail.com"}}
        self._failures: dict[str, tuple[int, int]] = {}
        self._wire()

    def fail_next(self, op: str, status: int, times: int = 1) -> None:
        self._failures[op] = (status, times)

    def _request(self, op: str, result: Any) -> MagicMock:
        request = MagicMock(name=f"request:{op}")

        def execute() -> Any:
            self.call_order.append((op,))
            status, times = self._failures.get(op, (0, 0))
            if times:
                self._failures[op] = (status, times - 1)
                raise HttpError(httplib2.Response({"status": status}), b"quota details")
            return result() if callable(result) else result

        request.execute.side_effect = execute
        return request

    def _wire(self) -> None:
        files = self.drive.files()
        files.create.side_effect = lambda **_: self._request("files.create", {"id": "file-1"})
        files.update.side_effect = lambda **_: self._request("files.update", {})
        self.drive.permissions().create.side_effect = lambda **_: self._request(
            "permissions.create", {}
        )
        self.drive.about().get.side_effect = lambda **_: self._request(
            "about.get", lambda: self.about_reply
        )
        spreadsheets = self.sheets.spreadsheets()
        spreadsheets.batchUpdate.side_effect = lambda spreadsheetId, body: self._request(
            "batchUpdate", self._batch_reply(body)
        )
        spreadsheets.values().batchUpdate.side_effect = lambda **_: self._request(
            "values.batchUpdate", {}
        )

    @staticmethod
    def _batch_reply(body: dict[str, Any]) -> dict[str, Any]:
        replies: list[dict[str, Any]] = []
        next_id = 100
        for request in body["requests"]:
            if "addSheet" in request:
                properties = {**request["addSheet"]["properties"], "sheetId": next_id}
                next_id += 1
                replies.append({"addSheet": {"properties": properties}})
            else:
                replies.append({})
        return {"replies": replies}


@pytest.fixture
def google():
    fake = FakeGoogle()
    module = "michael_s_molina.sqllab_gsheets.google_sheets"
    with (
        patch(
            f"{module}.build",
            side_effect=lambda name, *_, **__: fake.drive if name == "drive" else fake.sheets,
        ),
        patch(
            f"{module}.service_account.Credentials.from_service_account_info"
        ) as service_account_factory,
        patch(
            f"{module}.user_credentials.Credentials.from_authorized_user_info"
        ) as user_factory,
        patch(f"{module}.time.sleep", side_effect=fake.sleep_calls.append),
    ):
        fake.service_account_factory = service_account_factory
        fake.user_factory = user_factory
        yield fake
