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
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import Event
from typing import Any

from flask import g
from superset_core.exports.export import (
    as_user,
    build_tab_name,
    describe_dashboard_export,
    iter_dashboard_chart_results,
)
from superset_core.exports.types import ChartResult, DashboardExportInfo, SkipReason
from superset_core.tasks.decorators import get_context, task
from superset_core.tasks.types import TaskScope

from .about_tab import ABOUT_COLUMNS, ExportedTab, SkippedChart, build_about_tab
from .cells import query_rows, temporal_columns
from .config import DEFAULT_EXPORT_TIMEOUT_SECONDS
from .dashboard_permissions import dashboard_export_allowed
from .google_sheets import (
    MAX_CELLS,
    Tab,
    create_spreadsheet,
    query_cells,
    tab_cells,
    tab_label,
)

logger = logging.getLogger(__name__)

ACCESS_DENIED = "You no longer have permission to export this dashboard."
EXPORT_FAILED = "Couldn't export the dashboard to Google Sheets. Try again later."
NO_CHARTS = "No dashboard charts could be exported to Google Sheets."
CELL_LIMIT = "The chart exceeds the remaining spreadsheet cell limit."
CHART_FAILED = "The chart could not be exported."
CANCELLED = "The dashboard export was cancelled or timed out."
METADATA_LIMIT = "The dashboard metadata exceeds the spreadsheet cell limit."


class DashboardExportError(RuntimeError):
    """A dashboard export failure whose message is safe to display."""


class DashboardExportAborted(DashboardExportError):
    """Cooperative cancellation after the task's abort handler has fired."""


@dataclass(frozen=True)
class _ChartTabs:
    tabs: list[Tab]
    mappings: list[ExportedTab]
    cells: int


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
    email = getattr(g.user, "email", None)
    if not dashboard_export_allowed(email, dashboard_id):
        raise DashboardExportError(ACCESS_DENIED)
    return str(email)


def _classify(
    chart: ChartResult, info: DashboardExportInfo, used: set[str], remaining: int
) -> _ChartTabs | SkippedChart:
    """Convert a chart only if all its tabs and metadata fit the remaining budget."""
    if chart.skipped_reason is not None:
        return _skip(chart, _skip_reason(chart.skipped_reason))
    if not chart.queries or any(not query.colnames for query in chart.queries):
        return _skip(chart, CHART_FAILED)
    # The first mapping row is reserved; only extra tab mappings count here.
    cells = sum(query_cells(query) for query in chart.queries) + ABOUT_COLUMNS * (
        len(chart.queries) - 1
    )
    if cells > remaining:
        return _skip(chart, CELL_LIMIT)

    try:
        # Don't allocate names until every query is convertible.
        tabs = [
            Tab("", query_rows(query), temporal_columns(query))
            for query in chart.queries
        ]
    except Exception:
        logger.exception("Couldn't prepare dashboard chart %s", chart.chart_id)
        return _skip(chart, CHART_FAILED)

    mappings: list[ExportedTab] = []
    for index, tab in enumerate(tabs):
        name = build_tab_name(
            tab_label(chart.chart_id, chart.chart_name, index), used, 100
        )
        tabs[index] = Tab(name, tab.rows, tab.temporal_columns)
        mappings.append(
            ExportedTab(
                name,
                chart.chart_name,
                info.chart_urls.get(chart.chart_id, ""),
                len(tab.rows) - 1,
            )
        )
    return _ChartTabs(tabs, mappings, cells)


def _check_between_charts(
    results: Iterator[ChartResult], check_abort: Callable[[], None]
) -> Iterator[ChartResult]:
    while True:
        check_abort()
        try:
            chart = next(results)
        except StopIteration:
            return
        check_abort()
        yield chart


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
            chart_count = len(info.chart_urls)
            # Every chart needs at least one About row, even if it is skipped.
            # The base tab also includes the "None" row for zero skipped charts.
            about_base = build_about_tab(info, exported_at, [], [])
            remaining = MAX_CELLS - tab_cells(about_base) - chart_count * ABOUT_COLUMNS
            if remaining < 0:
                raise DashboardExportError(METADATA_LIMIT)
            results = iter(iter_dashboard_chart_results(dashboard_id, active_data_mask))
            for processed, chart in enumerate(
                _check_between_charts(results, check_abort), 1
            ):
                if processed > chart_count:
                    # Reserve a row for charts added after metadata was read.
                    remaining -= ABOUT_COLUMNS
                    if remaining < 0:
                        raise DashboardExportError(METADATA_LIMIT)
                result = _classify(chart, info, used, remaining)
                if isinstance(result, SkippedChart):
                    if not skipped:
                        # A skip replaces the placeholder "None" row.
                        remaining += ABOUT_COLUMNS
                    skipped.append(result)
                else:
                    charts.append(result)
                    remaining -= result.cells
                context.update_task(
                    progress=0.8 * min(processed / max(1, chart_count), 1)
                )

            if not charts:
                raise DashboardExportError(NO_CHARTS)
            check_abort()
            email = _require_access(dashboard_id)
            about = build_about_tab(
                info,
                exported_at,
                [mapping for chart in charts for mapping in chart.mappings],
                skipped,
            )
            context.update_task(progress=0.85)
            url = create_spreadsheet(
                f"{info.title} · {exported_at.strftime('%Y-%m-%d %H:%M')} UTC",
                [about, *(tab for chart in charts for tab in chart.tabs)],
                email,
                checkpoint=check_abort,
            )
            # The spreadsheet is committed once sharing completes. Publish its
            # URL even if an abort arrives after the final write.
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
        if isinstance(ex, DashboardExportAborted):
            raise
        if aborted.is_set():
            raise DashboardExportAborted(message) from ex
        if isinstance(ex, DashboardExportError):
            raise
        logger.exception("Dashboard Google Sheets export failed")
        raise DashboardExportError(message) from ex
