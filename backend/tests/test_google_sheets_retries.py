# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Exercise ambiguous Google responses after the server has applied a mutation."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import httplib2
import pytest
from flask import Flask
from googleapiclient.errors import HttpError

from michael_s_molina.sqllab_gsheets import google_sheets as writer


def _http_error(status: int) -> HttpError:
    """Construct the same HTTP exception type as Google's client."""
    return HttpError(httplib2.Response({"status": status}), b"Google error")


@dataclass
class _Failure:
    """An error returned before or after an operation's server-side effect."""

    error: Exception
    after_commit: bool = True
    remaining: int = 1


class _Request:
    """Keep request construction separate from repeated network executions."""

    def __init__(self, execute: Callable[[], dict[str, Any]]) -> None:
        self._execute = execute

    def execute(self, num_retries: int = 0) -> dict[str, Any]:
        """Apply the operation once; the application decides whether to retry."""
        assert num_retries == 0
        return self._execute()


class _RetryGoogle:
    """Record persistent effects, including effects whose response was lost."""

    def __init__(self) -> None:
        self.drive = MagicMock(name="drive")
        self.sheets = MagicMock(name="sheets")
        self.executions: Counter[str] = Counter()
        self.calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.failures: dict[str, _Failure] = {}
        self.created: dict[str, dict[str, Any]] = {}
        self.grids: dict[str, dict[str, int]] = {}
        self.values: dict[str, list[dict[str, Any]]] = {}
        self.formats: dict[str, list[dict[str, Any]]] = {}
        self.shared: set[str] = set()
        self.trashed: set[str] = set()
        self.sleeps: list[float] = []
        self.drive_id: str | None = "drive-1"
        self.list_pages: dict[str | None, dict[str, Any]] | None = None
        files = self.drive.files()
        files.create.side_effect = self._create
        files.get.side_effect = self._get
        files.list.side_effect = self._list
        files.update.side_effect = self._trash
        self.drive.permissions().create.side_effect = self._share
        spreadsheets = self.sheets.spreadsheets()
        spreadsheets.batchUpdate.side_effect = self._batch
        spreadsheets.values().batchUpdate.side_effect = self._values

    def fail(
        self, operation: str, error: Exception, *, after_commit: bool = True
    ) -> None:
        """Lose one response while optionally preserving its committed effect."""
        self.failures[operation] = _Failure(error, after_commit)

    def build(self, name: str, *_args: Any, **_kwargs: Any) -> MagicMock:
        """Provide either of the two Google service clients."""
        assert name in {"drive", "sheets"}
        assert isinstance(_kwargs.get("http"), writer.GoogleHttp)
        assert "credentials" not in _kwargs
        return self.drive if name == "drive" else self.sheets

    def _request(
        self,
        operation: str,
        kwargs: dict[str, Any],
        effect: Callable[[], dict[str, Any]],
    ) -> _Request:
        """Execute effects before ambiguous failures, rather than failing first."""

        def execute() -> dict[str, Any]:
            """Record each execution, including retries of the same request."""
            self.executions[operation] += 1
            self.calls[operation].append(deepcopy(kwargs))
            failure = self.failures.get(operation)
            should_fail = failure is not None and failure.remaining > 0
            if should_fail and failure is not None:
                failure.remaining -= 1
                if not failure.after_commit:
                    raise failure.error
            result = effect()
            if should_fail and failure is not None:
                raise failure.error
            return result

        return _Request(execute)

    def _create(self, **kwargs: Any) -> _Request:
        """Allocate a new file for every execution, as a non-idempotent POST does."""

        def effect() -> dict[str, Any]:
            """Persist the file even when the creation response is lost."""
            file_id = f"file-{len(self.created) + 1}"
            self.created[file_id] = deepcopy(kwargs["body"])
            self.grids[file_id] = {"Sheet1": 0}
            return {"id": file_id}

        return self._request("files.create", kwargs, effect)

    def _get(self, **kwargs: Any) -> _Request:
        """Report whether the configured folder belongs to a Shared Drive."""
        return self._request(
            "files.get",
            kwargs,
            lambda: {"driveId": self.drive_id} if self.drive_id else {},
        )

    def _list(self, **kwargs: Any) -> _Request:
        """Find committed files by their private creation marker."""

        def effect() -> dict[str, Any]:
            """Allow explicit empty, ambiguous, and paginated search responses."""
            if self.list_pages is not None:
                return deepcopy(self.list_pages[kwargs.get("pageToken")])
            query = kwargs["q"]
            matches = [
                {"id": file_id}
                for file_id, body in self.created.items()
                if (marker := body.get("appProperties", {}).get("superset_export_id"))
                and marker in query
            ]
            return {"files": matches}

        return self._request("files.list", kwargs, effect)

    def _trash(self, **kwargs: Any) -> _Request:
        """Apply an absolute, repeatable trash update."""

        def effect() -> dict[str, Any]:
            """Record the identity of the file cleaned up."""
            self.trashed.add(kwargs["fileId"])
            return {}

        return self._request("files.update", kwargs, effect)

    def _batch(self, **kwargs: Any) -> _Request:
        """Distinguish structural mutations from repeatable formatting updates."""
        requests = kwargs["body"]["requests"]
        structural = any(
            "addSheet" in request or "deleteSheet" in request for request in requests
        )

        def effect() -> dict[str, Any]:
            """Commit valid structural batches atomically, like Google Sheets."""
            file_id = kwargs["spreadsheetId"]
            if not structural:
                self.formats[file_id] = deepcopy(requests)
                return {}
            grid = dict(self.grids[file_id])
            replies = []
            for request in requests:
                if "addSheet" in request:
                    properties = request["addSheet"]["properties"]
                    if properties["title"] in grid:
                        raise _http_error(400)
                    sheet_id = max(grid.values(), default=99) + 100
                    grid[properties["title"]] = sheet_id
                    replies.append(
                        {
                            "addSheet": {
                                "properties": {**properties, "sheetId": sheet_id}
                            }
                        }
                    )
                elif "deleteSheet" in request:
                    sheet_id = request["deleteSheet"]["sheetId"]
                    if sheet_id not in grid.values():
                        raise _http_error(400)
                    grid = {
                        name: value for name, value in grid.items() if value != sheet_id
                    }
                    replies.append({})
            self.grids[file_id] = grid
            return {"replies": replies}

        return self._request("structure" if structural else "format", kwargs, effect)

    def _values(self, **kwargs: Any) -> _Request:
        """Overwrite absolute ranges, so replay cannot append duplicate rows."""

        def effect() -> dict[str, Any]:
            """Persist the values before optionally losing their response."""
            self.values[kwargs["spreadsheetId"]] = deepcopy(kwargs["body"]["data"])
            return {}

        return self._request("values", kwargs, effect)

    def _share(self, **kwargs: Any) -> _Request:
        """Record sharing before optionally losing its response."""

        def effect() -> dict[str, Any]:
            """Persist the recipient grant independently of response delivery."""
            self.shared.add(kwargs["fileId"])
            return {}

        return self._request("permissions.create", kwargs, effect)


@pytest.fixture
def retry_google(app: Flask, monkeypatch: pytest.MonkeyPatch) -> _RetryGoogle:
    """Replace Google transport and delays while retaining the real export flow."""
    google = _RetryGoogle()
    monkeypatch.setattr(writer, "build", google.build)
    monkeypatch.setattr(
        writer.auth.service_account.Credentials,
        "from_service_account_info",
        MagicMock(return_value=object()),
    )
    monkeypatch.setattr(writer.time, "sleep", google.sleeps.append)
    return google


def _export() -> str:
    """Export one small tab through the public writer."""
    return writer.create_spreadsheet(
        "Sales", [writer.Tab("Sales", [["a"], [1]], [])], "u@x.com"
    )


@pytest.mark.parametrize("failure", [500, 503, "transport"])
@pytest.mark.parametrize("drive_id", ["drive-1", None])
def test_committed_creation_recovers_original_without_replaying(
    retry_google: _RetryGoogle, failure: int | str, drive_id: str | None
) -> None:
    """A lost creation response must not allocate another spreadsheet."""
    google = retry_google
    google.drive_id = drive_id
    error = (
        OSError("connection lost")
        if failure == "transport"
        else _http_error(int(failure))
    )
    google.fail("files.create", error)

    assert _export() == "https://docs.google.com/spreadsheets/d/file-1/edit"
    assert google.executions["files.create"] == 1
    assert list(google.created) == ["file-1"]
    assert google.shared == {"file-1"} and not google.trashed
    marker = google.created["file-1"]["appProperties"]["superset_export_id"]
    assert isinstance(marker, str) and marker
    parent = google.calls["files.get"][0]
    assert parent["fileId"] == "folder-1" and parent["supportsAllDrives"] is True
    assert "driveId" in parent["fields"]
    lookup = google.calls["files.list"][0]
    assert all(
        term in lookup["q"]
        for term in (
            marker,
            "superset_export_id",
            "folder-1",
            "application/vnd.google-apps.spreadsheet",
            "trashed",
        )
    )
    assert lookup["corpora"] == ("drive" if drive_id else "user")
    if drive_id:
        assert lookup["driveId"] == drive_id
    else:
        assert not lookup.get("driveId")
    assert lookup["supportsAllDrives"] is True
    assert lookup["includeItemsFromAllDrives"] is True
    assert all(
        field in lookup["fields"]
        for field in ("files(id)", "nextPageToken", "incompleteSearch")
    )


@pytest.mark.parametrize(
    "reply",
    [
        {"files": []},
        {"files": [{"id": "file-1"}, {"id": "file-2"}]},
        {"files": [{"id": "file-1"}], "incompleteSearch": True},
    ],
    ids=["missing", "multiple", "incomplete"],
)
def test_ambiguous_lookup_never_authorizes_another_creation(
    retry_google: _RetryGoogle, reply: dict[str, Any]
) -> None:
    """Uncertain search results must fail without creating or sharing a second file."""
    google = retry_google
    google.fail("files.create", _http_error(500))
    google.list_pages = {None: reply}

    with pytest.raises(writer.GoogleExportError):
        _export()
    assert google.executions["files.create"] == 1
    assert list(google.created) == ["file-1"]
    assert not google.shared and not google.executions["structure"]


def test_lookup_failure_does_not_replay_creation(retry_google: _RetryGoogle) -> None:
    """Failure to reconcile an ambiguous creation cannot safely justify a retry."""
    google = retry_google
    google.fail("files.create", _http_error(503))
    google.fail("files.list", _http_error(403), after_commit=False)
    with pytest.raises(writer.GoogleExportError):
        _export()
    assert google.executions["files.create"] == 1
    assert list(google.created) == ["file-1"] and not google.shared


def test_recovery_follows_an_empty_page_token(retry_google: _RetryGoogle) -> None:
    """A partial empty page is not proof that the committed file is absent."""
    google = retry_google
    google.fail("files.create", _http_error(500))
    google.list_pages = {
        None: {"files": [], "nextPageToken": "next-page"},
        "next-page": {"files": [{"id": "file-1"}]},
    }
    assert _export().endswith("/file-1/edit")
    assert google.executions["files.create"] == 1
    assert [call.get("pageToken") for call in google.calls["files.list"]] == [
        None,
        "next-page",
    ]


def test_recovery_checks_later_pages_for_other_matches(
    retry_google: _RetryGoogle,
) -> None:
    """One match on an unfinished result page is not a unique recovered file."""
    google = retry_google
    google.fail("files.create", _http_error(500))
    google.list_pages = {
        None: {"files": [{"id": "file-1"}], "nextPageToken": "next-page"},
        "next-page": {"files": [{"id": "file-2"}]},
    }
    with pytest.raises(writer.GoogleExportError):
        _export()
    assert google.executions["files.create"] == 1
    assert google.executions["files.list"] == 2
    assert not google.shared and not google.executions["structure"]


def test_definitive_creation_rejection_does_not_search(
    retry_google: _RetryGoogle,
) -> None:
    """An explicit access rejection neither commits a file nor requires discovery."""
    google = retry_google
    google.fail("files.create", _http_error(403), after_commit=False)
    with pytest.raises(writer.GoogleExportError):
        _export()
    assert google.executions["files.create"] == 1
    assert not google.created and not google.executions["files.list"]
    assert not google.executions["files.get"] and not google.trashed


def test_later_failure_trashes_the_recovered_file(retry_google: _RetryGoogle) -> None:
    """Cleanup must use the recovered identity when a subsequent operation fails."""
    google = retry_google
    google.fail("files.create", _http_error(500))
    google.fail("values", _http_error(403), after_commit=False)
    with pytest.raises(writer.GoogleExportError):
        _export()
    assert google.executions["files.create"] == 1
    assert google.trashed == {"file-1"} and not google.shared


@pytest.mark.parametrize("operation", ["structure", "permissions.create"])
def test_non_idempotent_mutations_are_not_replayed(
    retry_google: _RetryGoogle, operation: str
) -> None:
    """A committed structural or sharing mutation with a lost response runs once."""
    google = retry_google
    google.fail(operation, _http_error(500))
    with pytest.raises(writer.GoogleExportError):
        _export()
    assert google.executions[operation] == 1
    assert google.trashed == {"file-1"}


@pytest.mark.parametrize("operation", ["values", "format"])
def test_absolute_writes_can_retry_after_a_committed_error(
    retry_google: _RetryGoogle, operation: str
) -> None:
    """Replaying absolute value or format assignments leaves one completed export."""
    google = retry_google
    google.fail(operation, _http_error(500))
    assert _export().endswith("/file-1/edit")
    assert google.executions[operation] == 2
    assert list(google.created) == ["file-1"] and google.shared == {"file-1"}
    assert google.values["file-1"][0]["values"] == [["a"], [1]]
    assert google.formats["file-1"] and not google.trashed


def test_trashing_can_retry_after_a_committed_error(retry_google: _RetryGoogle) -> None:
    """The same absolute trash assignment remains safe after a lost response."""
    google = retry_google
    google.fail("values", _http_error(403), after_commit=False)
    google.fail("files.update", _http_error(503))
    with pytest.raises(writer.GoogleExportError):
        _export()
    assert google.executions["files.update"] == 2
    assert google.trashed == {"file-1"} and not google.shared


def test_creation_markers_are_unique_per_export(retry_google: _RetryGoogle) -> None:
    """Recovery metadata must distinguish two intentional exports with one title."""
    _export()
    _export()
    markers = [
        body["appProperties"]["superset_export_id"]
        for body in retry_google.created.values()
    ]
    assert len(markers) == 2 and all(
        isinstance(marker, str) and marker for marker in markers
    )
    assert markers[0] != markers[1]
