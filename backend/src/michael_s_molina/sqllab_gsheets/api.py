"""SQL Lab export to Google Sheets: the REST endpoint and the export flow behind it."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, cast

import pandas as pd
from flask import current_app, g, request, Response
from flask_appbuilder.api import expose, permission_name, protect
from flask_babel import gettext as __
from superset_core.common.daos import DatabaseDAO
from superset_core.exports.export import can_export_data
from superset_core.queries.types import QueryOptions, QueryStatus
from superset_core.rest_api.api import RestApi
from superset_core.rest_api.decorators import api

from .cells import dataframe_rows
from .config import sql_lab_max_rows
from .errors import ExportRejected, GENERIC_FAILURE, INVALID_REQUEST
from .google_sheets import (
    check_cell_limit,
    create_spreadsheet,
    GoogleExportError,
    Tab,
)

logger = logging.getLogger(__name__)

SQL_ACCESS_DENIED = "You don't have access to run this query."


def execute_query(
    database: Any,
    sql: str,
    catalog: str | None,
    schema: str | None,
) -> pd.DataFrame:
    logger.info("Executing query on database %s", database.id)

    # One row past the cap, so an oversize result is detectable. template_params={}
    # makes the executor render Jinja exactly as check_sql_access's check did.
    options = QueryOptions(
        catalog=catalog,
        schema=schema,
        limit=sql_lab_max_rows() + 1,
        template_params={},
    )
    result = database.execute(sql, options)

    if result.status != QueryStatus.SUCCESS:
        raise ValueError(result.error_message or "Query execution failed")

    if not result.statements or result.statements[0].data is None:
        return pd.DataFrame()

    return result.statements[0].data


def check_sql_access(
    database: Any,
    sql: str,
    catalog: str | None,
    schema: str | None,
) -> None:
    """Refuse SQL the user couldn't run in SQL Lab itself."""
    # FAB attaches appbuilder to the app at runtime; Flask's types don't declare it.
    sm = cast(Any, current_app).appbuilder.sm
    if not sm.can_access("can_execute_sql_query", "SQLLab"):
        raise ExportRejected(403, SQL_ACCESS_DENIED)
    if not database.expose_in_sqllab:
        raise ExportRejected(403, SQL_ACCESS_DENIED)
    try:
        # SQL Lab's own validator passes force_dataset_match for raw row reads.
        sm.raise_for_access(
            database=database,
            sql=sql,
            catalog=catalog,
            schema=schema,
            template_params={},
            force_dataset_match=True,
        )
    except Exception as ex:
        # Superset is not importable here, so the class is matched by name.
        if type(ex).__name__ == "SupersetSecurityException":
            raise ExportRejected(403, SQL_ACCESS_DENIED) from ex
        logger.exception("SQL Lab access check failed during Google Sheets export")
        raise ExportRejected(500, GENERIC_FAILURE) from ex


def export_sql(
    database: Any,
    sql: str,
    catalog: str | None,
    schema: str | None,
    email: str | None,
    now: datetime,
) -> tuple[str, int]:
    if not email or not can_export_data():
        raise ExportRejected(403, "You don't have permission to export to Google Sheets.")
    check_sql_access(database, sql, catalog, schema)

    try:
        df = execute_query(database, sql, catalog, schema)
    except Exception as ex:
        logger.exception("SQL Lab query failed during Google Sheets export")
        raise ExportRejected(500, GENERIC_FAILURE) from ex

    limit = sql_lab_max_rows()
    if len(df.index) > limit:
        raise ExportRejected(
            400,
            f"Too large for Google Sheets (more than {limit} rows). "
            "Use Export to .CSV instead.",
        )

    rows, temporal = dataframe_rows(df)
    tabs = [Tab("Results", rows, temporal)]
    check_cell_limit(tabs)
    title = f"SQL Lab export · {now.strftime('%Y-%m-%d %H:%M')} UTC"

    try:
        url = create_spreadsheet(title, tabs, email)
    except GoogleExportError as ex:
        logger.exception("Google Sheets export failed")
        raise ExportRejected(500, GENERIC_FAILURE) from ex

    return url, len(df.index)


@api(id="sqllab_gsheets", name="SQL Lab Google Sheets Export")
class GSheetsExportAPI(RestApi):
    class_permission_name = "GoogleSheets"

    @expose("/export/", methods=("POST",))
    @protect()
    @permission_name("export")
    def export_gsheets(self) -> Response:
        """Export SQL query results to Google Sheets.

        Request body:
            sql: SQL query to execute
            databaseId: Database ID to execute against
            catalog: Optional catalog name
            schema: Optional schema name
        """
        try:
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                raise ExportRejected(400, INVALID_REQUEST)

            sql = body.get("sql")
            database_id = body.get("databaseId")

            if not sql:
                return self.response(400, message=__("SQL query is required"))

            if not database_id:
                return self.response(400, message=__("Database ID is required"))

            database = DatabaseDAO.find_one_or_none(id=database_id)
            if database is None:
                return self.response(404, message=__("Database not found"))

            url, row_count = export_sql(
                database,
                sql,
                body.get("catalog"),
                body.get("schema"),
                g.user.email,
                datetime.now(timezone.utc),
            )
        except ExportRejected as ex:
            return self.response(ex.status, message=ex.message)
        except Exception:
            logger.exception("Unexpected error in Google Sheets export")
            return self.response(500, message=GENERIC_FAILURE)
        return self.response(200, spreadsheet_url=url, row_count=row_count)
