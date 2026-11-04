"""Chart export to Google Sheets: the REST endpoints and the export flow behind them."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from flask import g, request, Response
from flask_appbuilder.api import expose, permission_name, protect
from superset_core.exports.export import (
    build_tab_name,
    can_export_data,
    run_query_context,
)
from superset_core.rest_api.api import RestApi
from superset_core.rest_api.decorators import api

from .cells import query_rows, temporal_columns
from .config import sync_max_rows
from .errors import ExportRejected
from .google_sheets import (
    create_spreadsheet,
    Tab,
    tab_label,
)

logger = logging.getLogger(__name__)

TAB_NAME_MAX_LEN = 100
UNTITLED = "Untitled chart"
GENERIC_FAILURE = "Couldn't create the Google Sheet. Try again later."
INVALID_REQUEST = "Invalid export request."


def capabilities(email: str | None) -> dict[str, bool]:
    return {"can_export": can_export_data() and bool(email)}


def _run(payload: dict[str, Any]) -> list[Any]:
    try:
        return run_query_context(payload)
    except Exception as ex:
        # Superset is not importable here, so the class is matched by name.
        name = type(ex).__name__
        if name == "SupersetSecurityException":
            raise ExportRejected(403, "You don't have access to this chart.") from ex
        if name in ("ValidationError", "ValueError"):
            logger.warning("Rejected chart query payload: %s", ex)
            raise ExportRejected(400, INVALID_REQUEST) from ex
        logger.exception("Chart query failed during Google Sheets export")
        raise ExportRejected(500, GENERIC_FAILURE) from ex


def _validate(payload: Any, chart_name: Any) -> None:
    form_data = payload.get("form_data") if isinstance(payload, dict) else None
    valid = (
        isinstance(payload, dict)
        and bool(payload)
        and (form_data is None or isinstance(form_data, dict))
        and (chart_name is None or isinstance(chart_name, str))
    )
    if not valid:
        raise ExportRejected(400, INVALID_REQUEST)


def _tabs(payload: dict[str, Any], name: str, results: list[Any]) -> list[Tab]:
    chart_id = (payload.get("form_data") or {}).get("slice_id")
    used: set[str] = set()
    return [
        Tab(
            build_tab_name(tab_label(chart_id, name, index), used, TAB_NAME_MAX_LEN),
            query_rows(result),
            temporal_columns(result),
        )
        for index, result in enumerate(results)
    ]


def export_chart(
    payload: Any,
    chart_name: Any,
    email: str | None,
    now: datetime,
) -> str:
    if not email or not can_export_data():
        raise ExportRejected(403, "You don't have permission to export to Google Sheets.")
    _validate(payload, chart_name)

    results = _run(payload)

    total = sum(len(result.data) for result in results)
    limit = sync_max_rows()
    if total > limit:
        raise ExportRejected(
            400,
            f"Too large for Google Sheets ({total} rows, limit {limit}). "
            "Use Export to .CSV instead.",
        )

    name = chart_name or UNTITLED
    title = f"{name} · {now.strftime('%Y-%m-%d %H:%M')} UTC"

    try:
        return create_spreadsheet(title, _tabs(payload, name, results), email)
    except Exception as ex:
        logger.exception("Google Sheets export failed")
        raise ExportRejected(500, GENERIC_FAILURE) from ex


@api(id="sqllab_gsheets_chart", name="Google Sheets chart export")
class ChartExportAPI(RestApi):
    class_permission_name = "GoogleSheets"

    @expose("/chart/", methods=("POST",))
    @protect()
    @permission_name("export")
    def export_to_sheets(self) -> Response:
        """Export a chart's query results to a new Google Sheet.

        Request body:
            payload: The chart's query context, as sent to /api/v1/chart/data
            chartName: Name used for the spreadsheet title and tabs
        """
        try:
            body = request.json
            if not isinstance(body, dict):
                raise ExportRejected(400, INVALID_REQUEST)
            url = export_chart(
                body.get("payload"),
                body.get("chartName"),
                g.user.email,
                datetime.now(timezone.utc),
            )
        except ExportRejected as ex:
            return self.response(ex.status, message=ex.message)
        except Exception:
            logger.exception("Unexpected error in Google Sheets export")
            return self.response(500, message=GENERIC_FAILURE)
        return self.response(200, url=url)

    @expose("/chart/capabilities", methods=("GET",))
    @protect()
    @permission_name("export")
    def chart_capabilities(self) -> Response:
        """Report whether the current user can export charts to Google Sheets."""
        try:
            return self.response(200, **capabilities(g.user.email))
        except Exception:
            logger.exception("Unexpected error in Google Sheets capabilities check")
            return self.response(500, message=GENERIC_FAILURE)
