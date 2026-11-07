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
"""Authorize dashboard exports before submitting private background work."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from flask import Flask, g
from michael_s_molina.sqllab_gsheets import dashboard_api as api_module
from michael_s_molina.sqllab_gsheets import dashboard_task
from michael_s_molina.sqllab_gsheets.errors import GENERIC_FAILURE, ExportRejected
from michael_s_molina.sqllab_gsheets.filter_hash import dashboard_export_key
from superset_core.tasks.types import TaskOptions

TASK_ID = UUID("62ca910d-9fd0-4a20-9af9-dc132fd635d1")


@dataclass
class _Mocks:
    """Keep permission gates separate from scheduling and worker I/O."""

    worker: MagicMock
    data_permission: MagicMock
    dashboard_permission: MagicMock
    security_manager: MagicMock
    queries: MagicMock
    create: MagicMock


@pytest.fixture
def dashboard_mocks(app: Flask, monkeypatch: pytest.MonkeyPatch) -> _Mocks:
    """Use real validation and hashing while isolating authorization and execution."""
    worker = MagicMock()
    worker.schedule.return_value = SimpleNamespace(uuid=TASK_ID, properties_dict={})
    data_permission = MagicMock(return_value=True)
    dashboard_permission = MagicMock(return_value=True)
    sm = MagicMock()
    sm.has_access.return_value = True
    setattr(app, "appbuilder", SimpleNamespace(sm=sm))
    app.config["FEATURE_FLAGS"] = {"GLOBAL_TASK_FRAMEWORK": True}
    queries = MagicMock()
    create = MagicMock()
    monkeypatch.setattr(api_module, "run_dashboard_export", worker)
    monkeypatch.setattr(api_module, "can_export_data", data_permission)
    monkeypatch.setattr(api_module, "can_export_dashboard", dashboard_permission)
    monkeypatch.setattr(dashboard_task, "iter_dashboard_chart_results", queries)
    monkeypatch.setattr(dashboard_task, "create_spreadsheet", create)
    return _Mocks(worker, data_permission, dashboard_permission, sm, queries, create)


def _response_api() -> MagicMock:
    """Expose response values without constructing a running FAB application."""
    instance = MagicMock()
    instance.response.side_effect = lambda status, **body: (status, body)
    return instance


def test_submission_uses_server_identity_snapshot_and_timeout(
    app: Flask, dashboard_mocks: _Mocks
) -> None:
    """Caller-supplied identity cannot change the scheduled principal or dedup key."""
    app.config["GSHEETS_EXPORT_TIMEOUT_SECONDS"] = 123
    body: dict[str, Any] = {
        "dashboardId": 7,
        "userId": 999,
        "user_id": 999,
        "activeDataMask": {
            "f": {
                "extraFormData": {
                    "filters": [{"col": "region", "op": "IN", "val": ["CA"]}]
                },
                "filterState": {"value": ["CA"]},
                "ownState": {"search": "unused"},
            }
        },
    }
    endpoint = inspect.unwrap(api_module.DashboardExportAPI.export_to_sheets)
    with app.test_request_context(method="POST", json=body):
        g.user = SimpleNamespace(id=17, email="u@x.com")
        assert endpoint(_response_api()) == (
            202,
            {"task_id": str(TASK_ID), "already_running": False},
        )
    call = dashboard_mocks.worker.schedule.call_args
    assert call.args[0] == 7 and call.args[2] == 17
    snapshot = call.args[1]
    assert "ownState" not in snapshot["f"]
    options = call.kwargs["options"]
    assert isinstance(options, TaskOptions)
    assert options.timeout == 123
    assert options.task_key == dashboard_export_key(7, snapshot, 17)
    assert options.task_name
    body["activeDataMask"]["f"]["extraFormData"]["filters"][0]["val"].append("NY")
    assert snapshot["f"]["extraFormData"]["filters"][0]["val"] == ["CA"]
    dashboard_mocks.worker.assert_not_called()
    dashboard_mocks.queries.assert_not_called()
    dashboard_mocks.create.assert_not_called()


def test_repeated_submission_reports_the_joined_task(dashboard_mocks: _Mocks) -> None:
    """The response preserves the framework's UUID and deduplication indication."""
    dashboard_mocks.worker.schedule.side_effect = [
        SimpleNamespace(uuid=TASK_ID, properties_dict={"dedupe_count": 0}),
        SimpleNamespace(uuid=TASK_ID, properties_dict={"dedupe_count": 1}),
    ]
    first = api_module.start_dashboard_export({"dashboardId": 7}, 17, "u@x.com")
    repeated = api_module.start_dashboard_export({"dashboardId": 7}, 17, "u@x.com")
    assert first == {"task_id": str(TASK_ID), "already_running": False}
    assert repeated == {"task_id": str(TASK_ID), "already_running": True}
    calls = dashboard_mocks.worker.schedule.call_args_list
    assert calls[0].kwargs["options"].task_key == calls[1].kwargs["options"].task_key


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        "bad",
        {},
        {"dashboardId": None},
        {"dashboardId": True},
        {"dashboardId": 0},
        {"dashboardId": -1},
        {"dashboardId": "7"},
        {"dashboardId": 7.0},
        {"dashboardId": 7, "activeDataMask": None},
        {"dashboardId": 7, "activeDataMask": {"f": {"extraFormData": []}}},
    ],
)
def test_malformed_submission_is_400_without_work(
    dashboard_mocks: _Mocks, body: Any
) -> None:
    """Malformed identifiers and filter state never reach scheduling or Google."""
    with pytest.raises(ExportRejected) as error:
        api_module.start_dashboard_export(body, 17, "u@x.com")
    assert error.value.status == 400
    dashboard_mocks.worker.schedule.assert_not_called()
    dashboard_mocks.queries.assert_not_called()
    dashboard_mocks.create.assert_not_called()


@pytest.mark.parametrize(
    ("user_id", "email", "data_allowed", "task_allowed", "dashboard_allowed"),
    [
        (None, "u@x.com", True, True, True),
        (0, "u@x.com", True, True, True),
        (17, None, True, True, True),
        (17, "", True, True, True),
        (17, "u@x.com", False, True, True),
        (17, "u@x.com", True, False, True),
        (17, "u@x.com", True, True, False),
    ],
)
def test_missing_permission_is_403_without_work(
    dashboard_mocks: _Mocks,
    user_id: int | None,
    email: str | None,
    data_allowed: bool,
    task_allowed: bool,
    dashboard_allowed: bool,
) -> None:
    """Export, dashboard, identity and task-read permissions are all required."""
    dashboard_mocks.data_permission.return_value = data_allowed
    dashboard_mocks.security_manager.has_access.return_value = task_allowed
    dashboard_mocks.dashboard_permission.return_value = dashboard_allowed
    with pytest.raises(ExportRejected) as error:
        api_module.start_dashboard_export({"dashboardId": 7}, user_id, email)
    assert error.value.status == 403
    dashboard_mocks.worker.schedule.assert_not_called()
    dashboard_mocks.queries.assert_not_called()
    dashboard_mocks.create.assert_not_called()


def test_disabled_tasks_are_503_before_data_access(
    app: Flask, dashboard_mocks: _Mocks
) -> None:
    """A disabled task framework rejects work before querying or allocating a file."""
    app.config["FEATURE_FLAGS"] = {}
    with pytest.raises(ExportRejected) as error:
        api_module.start_dashboard_export({"dashboardId": 7}, 17, "u@x.com")
    assert (error.value.status, error.value.message) == (503, api_module.TASKS_DISABLED)
    dashboard_mocks.dashboard_permission.assert_not_called()
    dashboard_mocks.worker.schedule.assert_not_called()
    dashboard_mocks.queries.assert_not_called()
    dashboard_mocks.create.assert_not_called()


def test_dashboard_security_exception_is_safe_403(dashboard_mocks: _Mocks) -> None:
    """The host's security exception is translated without exposing its detail."""
    exception = type("SupersetSecurityException", (Exception,), {})
    dashboard_mocks.dashboard_permission.side_effect = exception("secret dataset")
    with pytest.raises(ExportRejected) as error:
        api_module.start_dashboard_export({"dashboardId": 7}, 17, "u@x.com")
    assert (error.value.status, error.value.message) == (
        403,
        api_module.PERMISSION_DENIED,
    )
    dashboard_mocks.worker.schedule.assert_not_called()


@pytest.mark.parametrize(
    "request_args",
    [
        {"data": "{not json", "content_type": "application/json"},
        {"data": "dashboardId=7", "content_type": "application/x-www-form-urlencoded"},
        {"json": [7]},
    ],
)
def test_endpoint_malformed_body_is_safe_400(
    app: Flask, dashboard_mocks: _Mocks, request_args: dict[str, Any]
) -> None:
    """Malformed HTTP bodies do not escape the API's client-error response."""
    endpoint = inspect.unwrap(api_module.DashboardExportAPI.export_to_sheets)
    with app.test_request_context(method="POST", **request_args):
        g.user = SimpleNamespace(id=17, email="u@x.com")
        assert endpoint(_response_api()) == (
            400,
            {"message": "Invalid export request."},
        )
    dashboard_mocks.worker.schedule.assert_not_called()


@pytest.mark.parametrize("failure_stage", ["access", "schedule"])
def test_endpoint_hides_unexpected_error_details(
    app: Flask, dashboard_mocks: _Mocks, failure_stage: str
) -> None:
    """Neither authorization infrastructure nor broker errors disclose internals."""
    target = (
        dashboard_mocks.dashboard_permission
        if failure_stage == "access"
        else dashboard_mocks.worker.schedule
    )
    target.side_effect = RuntimeError("secret database credentials")
    endpoint = inspect.unwrap(api_module.DashboardExportAPI.export_to_sheets)
    with app.test_request_context(method="POST", json={"dashboardId": 7}):
        g.user = SimpleNamespace(id=17, email="u@x.com")
        assert endpoint(_response_api()) == (500, {"message": GENERIC_FAILURE})


@pytest.mark.parametrize(
    ("email", "enabled", "task_read", "expected"),
    [
        ("u@x.com", True, True, True),
        (None, True, True, False),
        ("", True, True, False),
        ("u@x.com", False, True, False),
        ("u@x.com", True, False, False),
    ],
)
def test_capabilities_require_email_background_tasks_and_poll_access(
    app: Flask,
    dashboard_mocks: _Mocks,
    email: str | None,
    enabled: bool,
    task_read: bool,
    expected: bool,
) -> None:
    """The menu is offered only when the caller can start and observe its task."""
    app.config["FEATURE_FLAGS"] = {"GLOBAL_TASK_FRAMEWORK": enabled}
    dashboard_mocks.security_manager.has_access.return_value = task_read
    assert api_module.capabilities(email) == {"can_export": expected}
    dashboard_mocks.worker.schedule.assert_not_called()


@pytest.mark.parametrize(
    ("method_name", "path", "verb"),
    [
        ("export_to_sheets", "/dashboard/", "POST"),
        ("dashboard_capabilities", "/dashboard/capabilities", "GET"),
    ],
)
def test_routes_enforce_google_sheets_export_permission(
    app: Flask,
    dashboard_mocks: _Mocks,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    path: str,
    verb: str,
) -> None:
    """Keep the actual FAB protection wrapper on both exposed methods."""
    endpoint = getattr(api_module.DashboardExportAPI, method_name)
    assert getattr(endpoint, "_permission_name") == "export"
    assert (path, (verb,)) in getattr(endpoint, "_urls")
    instance = _response_api()
    instance.method_permission_name = {}
    instance.base_permissions = ["can_export"]
    instance.class_permission_name = "GoogleSheets"
    instance.allow_browser_login = True
    instance.response_403.return_value = (403, {})
    sm = dashboard_mocks.security_manager
    sm.is_item_public.return_value = False
    sm.has_access.return_value = False
    monkeypatch.setattr(
        "flask_appbuilder.security.decorators.current_user",
        SimpleNamespace(is_authenticated=True),
    )
    with app.test_request_context(path, method=verb):
        assert endpoint(instance) == (403, {})
    sm.has_access.assert_called_with("can_export", "GoogleSheets")
    dashboard_mocks.worker.schedule.assert_not_called()
