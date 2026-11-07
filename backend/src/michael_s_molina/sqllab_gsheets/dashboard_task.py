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
"""Export a dashboard through the public Superset task and export APIs."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import Event
from typing import Any, cast

from flask import current_app, g
from superset_core.exports.export import (
    as_user,
    build_tab_name,
    can_export_dashboard,
    can_export_data,
    describe_dashboard_export,
    iter_dashboard_chart_results,
)
from superset_core.exports.types import ChartResult, DashboardExportInfo, SkipReason
from superset_core.tasks.decorators import get_context, task
from superset_core.tasks.types import TaskScope

from .about_tab import ExportedTab, SkippedChart, build_about_tab
from .cells import query_rows, temporal_columns
from .config import DEFAULT_EXPORT_TIMEOUT_SECONDS
from .google_sheets import MAX_CELLS, Tab, create_spreadsheet, tab_label

logger = logging.getLogger(__name__)

ACCESS_DENIED = "You no longer have permission to export this dashboard."
EXPORT_FAILED = "Couldn't export the dashboard to Google Sheets. Try again later."
NO_CHARTS = "No dashboard charts could be exported to Google Sheets."
CELL_LIMIT = "The chart exceeds the remaining spreadsheet cell limit."
CHART_FAILED = "The chart could not be exported."
CANCELLED = "The dashboard export was cancelled or timed out."


class DashboardExportError(RuntimeError):
    """A dashboard export failure whose message is safe to display."""


class DashboardExportAborted(DashboardExportError):
    """Cooperative cancellation after the task's abort handler has fired."""


@dataclass(frozen=True)
class _ChartTabs:
    chart_id: int
    chart_name: str
    tabs: list[Tab]
    mappings: list[ExportedTab]
    cells: int


def _cell_count(tab: Tab) -> int:
    """Count allocated rectangular cells, including headers and blank values."""
    return len(tab.rows) * max((len(row) for row in tab.rows), default=0)


def _skip(chart: ChartResult, reason: str) -> SkippedChart:
    return SkippedChart(chart.chart_id, chart.chart_name, reason)


def _skip_reason(reason: SkipReason) -> str:
    if reason == SkipReason.NO_QUERY_CONTEXT:
        return "No saved query context is available."
    if reason == SkipReason.UNBOUNDED:
        return "The chart query is unbounded."
    return CHART_FAILED


def _require_access(dashboard_id: int) -> str:
    """Recheck queued work against the requesting user's effective grants."""
    sm = cast(Any, current_app).appbuilder.sm
    email = getattr(g.user, "email", None)
    if (
        not email
        or not sm.can_access("can_export", "GoogleSheets")
        or not can_export_data()
        or not can_export_dashboard(dashboard_id)
    ):
        raise DashboardExportError(ACCESS_DENIED)
    return str(email)


def _chart_tabs(
    chart: ChartResult, info: DashboardExportInfo, used: set[str]
) -> _ChartTabs:
    """Convert all queries together so a failing chart is never partially exported."""
    if not chart.queries or any(not query.colnames for query in chart.queries):
        raise ValueError("The chart has no tabular query results")
    tabs: list[Tab] = []
    mappings: list[ExportedTab] = []
    for index, query in enumerate(chart.queries):
        name = build_tab_name(
            tab_label(chart.chart_id, chart.chart_name, index), used, 100
        )
        tabs.append(Tab(name, query_rows(query), temporal_columns(query)))
        mappings.append(
            ExportedTab(
                name,
                chart.chart_name,
                info.chart_urls.get(chart.chart_id, ""),
                len(query.data),
            )
        )
    return _ChartTabs(
        chart.chart_id,
        chart.chart_name,
        tabs,
        mappings,
        sum(_cell_count(tab) for tab in tabs),
    )


def _about(
    info: DashboardExportInfo,
    exported_at: datetime,
    charts: list[_ChartTabs],
    skipped: list[SkippedChart],
) -> Tab:
    return build_about_tab(
        info,
        exported_at,
        [mapping for chart in charts for mapping in chart.mappings],
        skipped,
    )


def _total_cells(about: Tab, charts: list[_ChartTabs]) -> int:
    return _cell_count(about) + sum(chart.cells for chart in charts)


@task(
    name="sqllab_gsheets.export_dashboard",
    scope=TaskScope.PRIVATE,
    timeout=DEFAULT_EXPORT_TIMEOUT_SECONDS,
)
def run_dashboard_export(
    dashboard_id: int, active_data_mask: dict[str, Any], user_id: int
) -> None:
    """Query as the requester, skip failed charts, then publish one Google Sheet URL."""
    context = get_context()
    aborted = Event()
    context.on_abort(aborted.set)
    skipped: list[SkippedChart] = []

    def check_abort() -> None:
        if aborted.is_set():
            raise DashboardExportAborted(CANCELLED)

    try:
        with as_user(user_id):
            check_abort()
            email = _require_access(dashboard_id)
            info = describe_dashboard_export(dashboard_id, active_data_mask)
            check_abort()
            exported_at = datetime.now(timezone.utc)
            charts: list[_ChartTabs] = []
            used = {"about"}
            if _cell_count(_about(info, exported_at, charts, skipped)) > MAX_CELLS:
                raise DashboardExportError(
                    "The dashboard metadata exceeds the spreadsheet cell limit."
                )
            results = iter(iter_dashboard_chart_results(dashboard_id, active_data_mask))
            processed = 0
            while True:
                check_abort()
                try:
                    chart = next(results)
                except StopIteration:
                    break
                check_abort()
                processed += 1
                if chart.skipped_reason is not None:
                    skipped.append(_skip(chart, _skip_reason(chart.skipped_reason)))
                elif (
                    sum(
                        (len(query.data) + 1) * len(query.colnames)
                        for query in chart.queries
                    )
                    > MAX_CELLS
                ):
                    # Skip oversized results before copying their records into cells.
                    skipped.append(_skip(chart, CELL_LIMIT))
                else:
                    candidate_names = used.copy()
                    try:
                        candidate = _chart_tabs(chart, info, candidate_names)
                    except Exception:
                        logger.exception(
                            "Couldn't prepare dashboard chart %s", chart.chart_id
                        )
                        skipped.append(_skip(chart, CHART_FAILED))
                    else:
                        candidates = [*charts, candidate]
                        about = _about(info, exported_at, candidates, skipped)
                        if _total_cells(about, candidates) > MAX_CELLS:
                            skipped.append(_skip(chart, CELL_LIMIT))
                        else:
                            charts = candidates
                            used = candidate_names
                context.update_task(
                    progress=0.8 * min(processed / max(1, len(info.chart_urls)), 1)
                )

            # Later skip descriptions also consume cells; drop whole charts until
            # the final About tab and every data tab fit together.
            about = _about(info, exported_at, charts, skipped)
            while charts and _total_cells(about, charts) > MAX_CELLS:
                removed = charts.pop()
                skipped.append(
                    SkippedChart(removed.chart_id, removed.chart_name, CELL_LIMIT)
                )
                about = _about(info, exported_at, charts, skipped)
            if not charts:
                raise DashboardExportError(NO_CHARTS)
            check_abort()
            email = _require_access(dashboard_id)
            context.update_task(progress=0.85)
            url = create_spreadsheet(
                f"{info.title} · {exported_at.strftime('%Y-%m-%d %H:%M')} UTC",
                [about, *(tab for chart in charts for tab in chart.tabs)],
                email,
                checkpoint=check_abort,
            )
            # The writer's final checkpoint is the commit point; publishing its
            # returned URL must not strand a completed file on a late abort.
            context.update_task(
                progress=1.0,
                payload={
                    "url": url,
                    "exported_charts": len(charts),
                    "exported_tabs": sum(len(chart.tabs) for chart in charts),
                    "skipped_charts": [asdict(chart) for chart in skipped],
                },
                immediate=True,
            )
    except Exception as ex:
        message = (
            CANCELLED
            if aborted.is_set()
            else str(ex)
            if isinstance(ex, DashboardExportError)
            else EXPORT_FAILED
        )
        context.update_task(
            payload={
                "error_message": message,
                "skipped_charts": [asdict(chart) for chart in skipped],
            },
            immediate=True,
        )
        if aborted.is_set():
            raise DashboardExportAborted(message) from ex
        if isinstance(ex, DashboardExportError):
            raise
        logger.exception("Dashboard Google Sheets export failed")
        raise DashboardExportError(message) from ex
