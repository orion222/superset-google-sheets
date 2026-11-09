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

"""Authorize and schedule private dashboard exports."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, cast

from flask import Response, current_app, g, request
from flask_appbuilder.api import expose, permission_name, protect
from superset_core.exports.export import can_export_dashboard, can_export_data
from superset_core.rest_api.api import RestApi
from superset_core.rest_api.decorators import api
from superset_core.tasks.types import TaskOptions

from .config import background_tasks_enabled, export_timeout_seconds
from .dashboard_task import run_dashboard_export
from .errors import GENERIC_FAILURE, INVALID_REQUEST, ExportRejected
from .filter_hash import dashboard_export_key, snapshot_filters

if TYPE_CHECKING:
    from superset_core.tasks.models import Task

logger = logging.getLogger(__name__)
PERMISSION_DENIED = (
    "You don't have permission to export this dashboard to Google Sheets."
)
TASKS_DISABLED = "Dashboard export requires background tasks to be enabled."


class _DashboardScheduler(Protocol):
    """Include the scheduling-only options omitted from the core ParamSpec stub."""

    def schedule(
        self,
        dashboard_id: int,
        active_data_mask: dict[str, Any],
        user_id: int,
        *,
        options: TaskOptions,
    ) -> Task:
        """Schedule an export with its deduplication key and timeout."""
        ...


def capabilities(email: str | None) -> dict[str, bool]:
    """Hide the command when it cannot start or observe background exports."""
    return {
        "can_export": bool(email)
        and background_tasks_enabled()
        and cast(Any, current_app).appbuilder.sm.has_access("can_read", "Task")
    }


def start_dashboard_export(
    body: Any, user_id: int | None, email: str | None
) -> dict[str, Any]:
    """Snapshot filters and atomically create or join the requester's export."""
    if not user_id or not email or not can_export_data():
        raise ExportRejected(403, PERMISSION_DENIED)
    if not cast(Any, current_app).appbuilder.sm.has_access("can_read", "Task"):
        raise ExportRejected(403, PERMISSION_DENIED)
    if not background_tasks_enabled():
        raise ExportRejected(503, TASKS_DISABLED)
    if not isinstance(body, dict):
        raise ExportRejected(400, INVALID_REQUEST)
    dashboard_id = body.get("dashboardId")
    if (
        not isinstance(dashboard_id, int)
        or isinstance(dashboard_id, bool)
        or dashboard_id <= 0
    ):
        raise ExportRejected(400, INVALID_REQUEST)
    active_data_mask = snapshot_filters(body.get("activeDataMask", {}))
    try:
        permitted = can_export_dashboard(dashboard_id)
    except Exception as ex:
        if type(ex).__name__ == "SupersetSecurityException":
            raise ExportRejected(403, PERMISSION_DENIED) from ex
        raise
    if not permitted:
        raise ExportRejected(403, PERMISSION_DENIED)
    task = cast(_DashboardScheduler, run_dashboard_export).schedule(
        dashboard_id,
        active_data_mask,
        user_id,
        options=TaskOptions(
            task_key=dashboard_export_key(dashboard_id, active_data_mask, user_id),
            task_name="Export dashboard to Google Sheets",
            timeout=export_timeout_seconds(),
        ),
    )
    return {
        "task_id": str(task.uuid),
        "already_running": task.properties_dict.get("dedupe_count", 0) > 0,
    }


@api(id="sqllab_gsheets_dashboard", name="Google Sheets dashboard export")
class DashboardExportAPI(RestApi):
    """Dashboard exports use the same extension permission as chart exports."""

    class_permission_name = "GoogleSheets"

    @expose("/dashboard/", methods=("POST",))
    @protect()
    @permission_name("export")
    def export_to_sheets(self) -> Response:
        """Start a dashboard export using the authenticated user's filter state."""
        try:
            result = start_dashboard_export(
                request.get_json(silent=True), g.user.id, g.user.email
            )
        except ExportRejected as ex:
            return self.response(ex.status, message=ex.message)
        except Exception as ex:
            if type(ex).__name__ == "GlobalTaskFrameworkDisabledError":
                return self.response(503, message=TASKS_DISABLED)
            logger.exception("Couldn't schedule Google Sheets dashboard export")
            return self.response(500, message=GENERIC_FAILURE)
        return self.response(202, **result)

    @expose("/dashboard/capabilities", methods=("GET",))
    @protect()
    @permission_name("export")
    def dashboard_capabilities(self) -> Response:
        """Report whether background dashboard exports are available."""
        try:
            return self.response(200, **capabilities(g.user.email))
        except Exception:
            logger.exception("Couldn't check dashboard export capabilities")
            return self.response(500, message=GENERIC_FAILURE)
