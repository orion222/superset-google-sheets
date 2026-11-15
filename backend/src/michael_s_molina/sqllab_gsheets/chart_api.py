"""Chart request parsing and export flow for Google Sheets."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from superset_core.exports.export import (
    build_tab_name,
    run_query_context,
)
from superset_core.exports.types import ChartQueryResult

from .cells import query_rows, temporal_columns
from .chart import tab_label
from .config import sync_max_rows
from .errors import ExportRejected, INVALID_REQUEST, is_named_exception
from .export import (
    can_export_user,
    publish,
    reject_too_many_rows,
    require_exporter,
)
from .google_sheets import Tab

TAB_NAME_MAX_LEN = 100
UNTITLED = "Untitled chart"
ROW_RESULT_TYPES = frozenset(
    {"full", "results", "post_processed", "samples", "drill_detail"}
)


@dataclass(frozen=True)
class ChartExportRequest:
    payload: dict[str, Any]
    chart_name: str | None

    @classmethod
    def parse(cls, body: Any) -> ChartExportRequest:
        if not isinstance(body, dict):
            raise ExportRejected(400, INVALID_REQUEST)

        payload = body.get("payload")
        chart_name = body.get("chartName")
        if not isinstance(payload, dict) or not payload:
            raise ExportRejected(400, INVALID_REQUEST)
        if chart_name is not None and not isinstance(chart_name, str):
            raise ExportRejected(400, INVALID_REQUEST)

        form_data = payload.get("form_data")
        queries = payload.get("queries")
        if form_data is not None and not isinstance(form_data, dict):
            raise ExportRejected(400, INVALID_REQUEST)
        chart_id = form_data.get("slice_id") if form_data is not None else None
        if chart_id is not None and (
            not isinstance(chart_id, int) or isinstance(chart_id, bool)
        ):
            raise ExportRejected(400, INVALID_REQUEST)
        if queries is not None and (
            not isinstance(queries, list)
            or not all(isinstance(query, dict) for query in queries)
        ):
            raise ExportRejected(400, INVALID_REQUEST)

        result_types = [payload.get("result_type")]
        if queries:
            result_types.extend(query.get("result_type") for query in queries)
        if any(
            result_type is not None
            and (
                not isinstance(result_type, str)
                or result_type not in ROW_RESULT_TYPES
            )
            for result_type in result_types
        ):
            raise ExportRejected(400, INVALID_REQUEST)
        return cls(payload=payload, chart_name=chart_name)


def capabilities(email: str | None) -> dict[str, bool]:
    # The host disables the items for users who can't download, so they stay visible.
    return {"can_export": can_export_user(email)}


def _chart_id_from_payload(payload: dict[str, Any]) -> int | None:
    form_data = payload.get("form_data")
    if not isinstance(form_data, dict):
        return None
    chart_id = form_data.get("slice_id")
    if isinstance(chart_id, int) and not isinstance(chart_id, bool):
        return chart_id
    return None


def _run(payload: dict[str, Any]) -> list[ChartQueryResult]:
    try:
        return run_query_context(payload)
    except Exception as ex:
        # Superset is not importable here, so the class is matched by name.
        if is_named_exception(ex, "SupersetSecurityException"):
            raise ExportRejected(403, "You don't have access to this chart.") from ex
        if is_named_exception(ex, "ValidationError", module="marshmallow"):
            raise ExportRejected(400, INVALID_REQUEST) from ex
        raise


def _tabs(
    chart_request: ChartExportRequest, name: str, results: list[ChartQueryResult]
) -> list[Tab]:
    used: set[str] = set()
    chart_id = _chart_id_from_payload(chart_request.payload)
    return [
        Tab(
            build_tab_name(
                tab_label(chart_id, name, index),
                used,
                TAB_NAME_MAX_LEN,
            ),
            query_rows(result),
            temporal_columns(result),
        )
        for index, result in enumerate(results)
    ]


def _bounded_row_limit(row_limit: Any, row_cap: int) -> Any:
    if row_limit is None:
        return row_cap
    if isinstance(row_limit, bool):
        row_limit = int(row_limit)
    elif isinstance(row_limit, str):
        try:
            row_limit = int(row_limit)
        except ValueError:
            return row_limit
    elif isinstance(row_limit, float) and math.isfinite(row_limit):
        row_limit = int(row_limit)
    if not isinstance(row_limit, int):
        return row_limit
    return min(row_limit, row_cap) if row_limit > 0 else row_cap


def _capped_query_context(payload: dict[str, Any], row_cap: int) -> dict[str, Any]:
    queries = payload.get("queries")
    if not isinstance(queries, list):
        return payload

    capped_queries: list[Any] = []
    for query in queries:
        if not isinstance(query, dict):
            capped_queries.append(query)
            continue
        capped_query = dict(query)
        capped_query["row_limit"] = _bounded_row_limit(
            capped_query.get("row_limit"), row_cap
        )
        capped_queries.append(capped_query)

    return {**payload, "queries": capped_queries}


def export_chart(
    chart_request: ChartExportRequest,
    email: str | None,
    now: datetime,
) -> str:
    email = require_exporter(email)

    limit = sync_max_rows()
    payload = _capped_query_context(chart_request.payload, limit + 1)
    results = _run(payload)

    total = sum(len(result.data) for result in results)
    if total > limit:
        reject_too_many_rows(total, limit)

    name = chart_request.chart_name or UNTITLED
    tabs = _tabs(chart_request, name, results)
    return publish(name, tabs, email, now)
