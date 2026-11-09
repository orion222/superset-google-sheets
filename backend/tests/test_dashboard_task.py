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
"""Exercise the worker with public host APIs and Google I/O replaced."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from flask import Flask, g, has_request_context
from flask_appbuilder.security.manager import BaseSecurityManager
from michael_s_molina.sqllab_gsheets import dashboard_task as worker
from michael_s_molina.sqllab_gsheets.google_sheets import Tab
from superset_core.exports.types import (
    AppliedFilter,
    ChartQueryResult,
    ChartResult,
    DashboardExportInfo,
    GenericDataType,
    SkipReason,
)
from superset_core.tasks.types import TaskScope


class _Context:
    def __init__(self) -> None:
        self.payload: dict[str, Any] = {}
        self.abort: Callable[[], None] | None = None
        self.after_update: Callable[[], None] | None = None

    def on_abort(self, handler: Callable[[], None]) -> Callable[[], None]:
        self.abort = handler
        return handler

    def update_task(
        self,
        progress: float | int | tuple[int, int] | None = None,
        payload: dict[str, Any] | None = None,
        *,
        immediate: bool = False,
    ) -> None:
        self.payload.update(payload or {})
        if self.after_update is not None:
            self.after_update()


@dataclass
class _Mocks:
    context: _Context
    create: MagicMock
    results: MagicMock
    metadata: MagicMock
    permission: MagicMock
    data_permission: MagicMock
    dashboard_permission: MagicMock
    user_ids: list[int]


def _chart(chart_id: int = 7, name: str = "Sales", count: int = 1) -> ChartResult:
    return ChartResult(
        chart_id,
        name,
        [
            ChartQueryResult(
                ["value"],
                [GenericDataType.NUMERIC],
                [{"value": i} for i in range(count)],
            )
        ],
        None,
    )


@pytest.fixture
def m(app: Flask, monkeypatch: pytest.MonkeyPatch) -> _Mocks:
    context = _Context()
    create = MagicMock(
        return_value="https://docs.google.com/spreadsheets/d/file-1/edit"
    )
    results = MagicMock(side_effect=lambda *_: iter([_chart()]))
    metadata = MagicMock(
        return_value=DashboardExportInfo(
            "Revenue",
            "https://superset.test/dashboard/permalink/snapshot/",
            [AppliedFilter("Region", "EU")],
            {7: "/explore/?slice_id=7", 8: "/explore/?slice_id=8"},
        )
    )
    permission = MagicMock(return_value=True)
    data_permission = MagicMock(return_value=True)
    dashboard_permission = MagicMock(return_value=True)
    setattr(
        app, "appbuilder", SimpleNamespace(sm=SimpleNamespace(can_access=permission))
    )
    user_ids: list[int] = []
    g.user = SimpleNamespace(id=99, email="previous@x.com")

    @contextmanager
    def as_user(user_id: int) -> Iterator[None]:
        user_ids.append(user_id)
        previous = g.user
        g.user = SimpleNamespace(id=user_id, email="requester@x.com")
        try:
            yield
        finally:
            g.user = previous

    def tab_name(raw: str, used: set[str], limit: int) -> str:
        name = raw[:limit]
        suffix = 2
        while name.lower() in used:
            marker = f"~{suffix}"
            name = raw[: limit - len(marker)] + marker
            suffix += 1
        used.add(name.lower())
        return name

    monkeypatch.setattr(worker, "get_context", lambda: context)
    monkeypatch.setattr(worker, "as_user", as_user)
    monkeypatch.setattr(worker, "build_tab_name", tab_name)
    monkeypatch.setattr(worker, "can_export_data", data_permission)
    monkeypatch.setattr(worker, "can_export_dashboard", dashboard_permission)
    monkeypatch.setattr(worker, "describe_dashboard_export", metadata)
    monkeypatch.setattr(worker, "iter_dashboard_chart_results", results)
    monkeypatch.setattr(worker, "create_spreadsheet", create)
    return _Mocks(
        context,
        create,
        results,
        metadata,
        permission,
        data_permission,
        dashboard_permission,
        user_ids,
    )


def test_worker_is_private_and_runs_as_requester_with_live_filter_state(
    m: _Mocks,
) -> None:
    mask = {
        "filter": {"extraFormData": {"filters": [{"col": "region", "val": ["EU"]}]}}
    }
    worker.run_dashboard_export(4, mask, 42)

    assert getattr(worker.run_dashboard_export, "scope") is TaskScope.PRIVATE
    assert m.user_ids == [42]
    assert g.user.id == 99
    m.permission.assert_called_with("can_export", "GoogleSheets")
    m.metadata.assert_called_once_with(4, mask)
    m.results.assert_called_once_with(4, mask)
    args = m.create.call_args
    assert args.args[0].startswith("Revenue · ")
    tabs: list[Tab] = args.args[1]
    assert [tab.name for tab in tabs] == ["About", "7 - Sales"]
    assert ["Region", "EU", "", ""] in tabs[0].rows
    assert ["7 - Sales", "Sales", "/explore/?slice_id=7", 1] in tabs[0].rows
    assert args.args[2] == "requester@x.com"
    assert callable(args.kwargs["checkpoint"])
    assert m.context.payload == {
        "url": m.create.return_value,
        "exported_charts": 1,
        "exported_tabs": 1,
        "skipped_charts": [],
    }


def test_worker_checks_permissions_without_an_http_request(
    app: Flask, m: _Mocks, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Celery has an app context but no Flask-Login request principal."""
    sm = object.__new__(BaseSecurityManager)
    setattr(sm, "can_access", m.permission)
    monkeypatch.setattr(app, "appbuilder", SimpleNamespace(sm=sm))
    assert not has_request_context()

    worker.run_dashboard_export(4, {}, 42)

    m.permission.assert_called_with("can_export", "GoogleSheets")
    assert m.context.payload["url"] == m.create.return_value
    assert g.user.id == 99


@pytest.mark.parametrize(
    "permission", ["permission", "data_permission", "dashboard_permission"]
)
def test_revoked_permission_stops_before_metadata_or_google(
    m: _Mocks, permission: str
) -> None:
    getattr(m, permission).return_value = False
    with pytest.raises(worker.DashboardExportError, match=worker.ACCESS_DENIED):
        worker.run_dashboard_export(4, {}, 42)
    m.metadata.assert_not_called()
    m.results.assert_not_called()
    m.create.assert_not_called()
    assert g.user.id == 99


def test_permissions_are_checked_again_after_querying(m: _Mocks) -> None:
    m.dashboard_permission.side_effect = [True, False]
    with pytest.raises(worker.DashboardExportError, match=worker.ACCESS_DENIED):
        worker.run_dashboard_export(4, {}, 42)
    m.results.assert_called_once()
    m.create.assert_not_called()


def test_latest_requester_email_is_used_after_querying(m: _Mocks) -> None:
    def results(*_: object) -> Iterator[ChartResult]:
        assert g.user.id == 42
        g.user.email = "updated@x.com"
        yield _chart()

    m.results.side_effect = results
    worker.run_dashboard_export(4, {}, 42)
    assert m.create.call_args.args[2] == "updated@x.com"


def test_worker_skips_whole_chart_when_one_of_its_queries_cannot_convert(
    m: _Mocks,
) -> None:
    bad = ChartResult(
        8,
        "Bad date",
        [
            _chart().queries[0],
            ChartQueryResult(["date"], [GenericDataType.TEMPORAL], [{"date": 10**100}]),
        ],
        None,
    )
    m.results.side_effect = lambda *_: iter([bad, _chart()])
    worker.run_dashboard_export(4, {}, 42)

    assert [tab.name for tab in m.create.call_args.args[1]] == ["About", "7 - Sales"]
    assert m.context.payload["skipped_charts"] == [
        {
            "chart_id": 8,
            "chart_name": "Bad date",
            "reason": worker.CHART_FAILED,
        }
    ]


def test_cell_budget_includes_about_and_skips_oversized_chart(
    m: _Mocks, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "MAX_CELLS", 100)
    m.results.side_effect = lambda *_: iter([_chart(), _chart(8, "Large", count=100)])
    worker.run_dashboard_export(4, {}, 42)

    tabs = m.create.call_args.args[1]
    assert [tab.name for tab in tabs] == ["About", "7 - Sales"]
    assert sum(worker._cell_count(tab) for tab in tabs) <= 100
    assert m.context.payload["skipped_charts"] == [
        {
            "chart_id": 8,
            "chart_name": "Large",
            "reason": worker.CELL_LIMIT,
        }
    ]


def test_about_tab_name_is_reserved(m: _Mocks) -> None:
    m.results.side_effect = lambda *_: iter([_chart(0, "About")])
    worker.run_dashboard_export(4, {}, 42)
    assert [tab.name for tab in m.create.call_args.args[1]] == ["About", "About~2"]


def test_metadata_alone_over_limit_fails_before_querying(
    m: _Mocks, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "MAX_CELLS", 1)
    with pytest.raises(worker.DashboardExportError, match="metadata exceeds"):
        worker.run_dashboard_export(4, {}, 42)
    m.results.assert_not_called()
    m.create.assert_not_called()


def test_later_skip_rows_cannot_overflow_final_about_budget(
    m: _Mocks, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "MAX_CELLS", 54)
    m.results.side_effect = lambda *_: iter(
        [
            _chart(),
            ChartResult(8, "Broken", [], SkipReason.GENERAL),
            ChartResult(9, "Broken too", [], SkipReason.GENERAL),
        ]
    )
    with pytest.raises(worker.DashboardExportError, match=worker.NO_CHARTS):
        worker.run_dashboard_export(4, {}, 42)
    m.create.assert_not_called()
    assert m.context.payload["skipped_charts"][-1] == {
        "chart_id": 7,
        "chart_name": "Sales",
        "reason": worker.CELL_LIMIT,
    }


def test_chart_query_failures_are_described_without_exposing_exceptions(
    m: _Mocks,
) -> None:
    m.results.side_effect = lambda *_: iter(
        [
            ChartResult(8, "Broken", [], SkipReason.GENERAL),
            _chart(),
        ]
    )
    worker.run_dashboard_export(4, {}, 42)
    assert m.context.payload["skipped_charts"] == [
        {
            "chart_id": 8,
            "chart_name": "Broken",
            "reason": worker.CHART_FAILED,
        }
    ]


@pytest.mark.parametrize(
    "charts",
    [
        [],
        [ChartResult(8, "Broken", [], SkipReason.GENERAL)],
        [ChartResult(8, "Empty", [], None)],
    ],
)
def test_no_exportable_charts_fails_clearly_before_google(
    m: _Mocks, charts: list[ChartResult]
) -> None:
    m.results.side_effect = lambda *_: iter(charts)
    with pytest.raises(worker.DashboardExportError, match=worker.NO_CHARTS):
        worker.run_dashboard_export(4, {}, 42)
    m.create.assert_not_called()
    assert m.context.payload["error_message"] == worker.NO_CHARTS


def test_abort_stops_before_acquiring_another_chart(m: _Mocks) -> None:
    acquired: list[int] = []

    def results(*_: object) -> Iterator[ChartResult]:
        acquired.append(7)
        yield _chart()
        acquired.append(8)
        yield _chart(8)

    m.results.side_effect = results

    def abort_on_progress() -> None:
        assert m.context.abort is not None
        m.context.abort()

    m.context.after_update = abort_on_progress
    with pytest.raises(worker.DashboardExportAborted, match=worker.CANCELLED):
        worker.run_dashboard_export(4, {}, 42)
    assert acquired == [7]
    m.create.assert_not_called()


def test_writer_receives_abort_checkpoint_and_never_publishes_cancelled_link(
    m: _Mocks,
) -> None:
    def create(*args: object, checkpoint: Callable[[], None]) -> str:
        assert m.context.abort is not None
        m.context.abort()
        checkpoint()
        return "unreachable"

    m.create.side_effect = create
    with pytest.raises(worker.DashboardExportAborted, match=worker.CANCELLED):
        worker.run_dashboard_export(4, {}, 42)
    assert "url" not in m.context.payload
    assert m.context.payload["error_message"] == worker.CANCELLED


def test_google_error_has_a_safe_task_failure_message(m: _Mocks) -> None:
    m.create.side_effect = ValueError("secret-token")
    with pytest.raises(worker.DashboardExportError, match=worker.EXPORT_FAILED):
        worker.run_dashboard_export(4, {}, 42)
    assert m.context.payload["error_message"] == worker.EXPORT_FAILED
    assert "secret-token" not in str(m.context.payload)
