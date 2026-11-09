"""SQL Lab export to Google Sheets: the REST endpoint and SQL-specific flow."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

import pandas as pd
from flask import current_app, g, Response
from flask_appbuilder.api import expose, permission_name, protect
from flask_babel import gettext as __
from superset_core.common.daos import DatabaseDAO
from superset_core.queries.types import QueryOptions, QueryStatus
from superset_core.rest_api.api import RestApi
from superset_core.rest_api.decorators import api

from .cells import dataframe_rows
from .config import sql_lab_max_rows
from .errors import (
    ExportRejected,
    GENERIC_FAILURE,
    INVALID_REQUEST,
    is_security_exception,
    QUERY_FAILED,
    QueryFailed,
)
from .export import json_export_endpoint, publish, require_exporter
from .google_sheets import Tab

logger = logging.getLogger(__name__)

SQL_ACCESS_DENIED = "You don't have access to run this query."
TEMPLATED_SQL = (
    "Templated SQL can't be exported to Google Sheets. "
    "Remove the Jinja syntax and try again."
)
# The access check and execution receive fresh empty parameters, so neither can
# render Jinja differently after the query has been checked.
_JINJA_MARKERS = ("{{", "{%", "{#")


@dataclass(frozen=True)
class SqlExportRequest:
    sql: str
    database_id: int
    catalog: str | None
    schema: str | None


def parse_sql_export_request(body: dict[str, Any]) -> SqlExportRequest:
    sql = body.get("sql")
    if not sql:
        raise ExportRejected(400, __("SQL query is required"))
    if not isinstance(sql, str):
        raise ExportRejected(400, INVALID_REQUEST)

    database_id = body.get("databaseId")
    if not database_id:
        raise ExportRejected(400, __("Database ID is required"))
    if not isinstance(database_id, int) or isinstance(database_id, bool):
        raise ExportRejected(400, INVALID_REQUEST)

    catalog = body.get("catalog")
    schema = body.get("schema")
    if (catalog is not None and not isinstance(catalog, str)) or (
        schema is not None and not isinstance(schema, str)
    ):
        raise ExportRejected(400, INVALID_REQUEST)

    return SqlExportRequest(sql, database_id, catalog, schema)


def execute_query(
    database: Any,
    sql: str,
    catalog: str | None,
    schema: str | None,
    max_rows: int,
) -> pd.DataFrame:
    logger.info("Executing query on database %s", database.id)

    options = QueryOptions(
        catalog=catalog,
        schema=schema,
        limit=max_rows + 1,
        template_params=_plain_sql_template_params(),
    )
    result = database.execute(sql, options)

    if result.status != QueryStatus.SUCCESS:
        raise QueryFailed(result.error_message or "Query execution failed")

    data = next(
        (
            statement.data
            for statement in reversed(result.statements or [])
            if statement.data is not None
        ),
        None,
    )
    if data is None:
        return pd.DataFrame()

    if len(data.index) > max_rows:
        raise ExportRejected(
            400,
            f"Too large for Google Sheets (more than {max_rows} rows). "
            "Use Export to .CSV instead.",
        )

    return data


def _require_sql_lab_execute() -> Any:
    # FAB attaches appbuilder to the app at runtime; Flask's types don't declare it.
    sm = cast(Any, current_app).appbuilder.sm
    if not sm.can_access("can_execute_sql_query", "SQLLab"):
        raise ExportRejected(403, SQL_ACCESS_DENIED)
    return sm


def _plain_sql_template_params() -> dict[str, Any]:
    return {}


def _reject_templated_sql(sql: str) -> None:
    if any(marker in sql for marker in _JINJA_MARKERS):
        raise ExportRejected(400, TEMPLATED_SQL)


def check_sql_access(
    sm: Any,
    database: Any,
    sql: str,
    catalog: str | None,
    schema: str | None,
) -> None:
    """Refuse SQL the user couldn't run in SQL Lab itself."""
    if not database.expose_in_sqllab:
        raise ExportRejected(403, SQL_ACCESS_DENIED)
    try:
        # SQL Lab's own validator passes force_dataset_match for raw row reads.
        sm.raise_for_access(
            database=database,
            sql=sql,
            catalog=catalog,
            schema=schema,
            template_params=_plain_sql_template_params(),
            force_dataset_match=True,
        )
    except Exception as ex:
        # Superset is not importable here, so the class is matched by name.
        if is_security_exception(ex):
            raise ExportRejected(403, SQL_ACCESS_DENIED) from ex
        logger.exception("SQL Lab access check failed during Google Sheets export")
        raise ExportRejected(500, GENERIC_FAILURE) from ex


def export_sql(
    export_request: SqlExportRequest,
    email: str | None,
    now: datetime,
) -> tuple[str, int]:
    email = require_exporter(email)
    sql = export_request.sql
    _reject_templated_sql(sql)

    sm = _require_sql_lab_execute()
    database = DatabaseDAO.find_one_or_none(id=export_request.database_id)
    if database is None:
        raise ExportRejected(403, SQL_ACCESS_DENIED)
    check_sql_access(
        sm,
        database,
        sql,
        export_request.catalog,
        export_request.schema,
    )

    max_rows = sql_lab_max_rows()
    try:
        df = execute_query(
            database, sql, export_request.catalog, export_request.schema, max_rows
        )
    except ExportRejected:
        raise
    except QueryFailed as ex:
        raise ExportRejected(400, QUERY_FAILED) from ex
    except Exception as ex:
        logger.exception("SQL Lab query failed during Google Sheets export")
        raise ExportRejected(500, GENERIC_FAILURE) from ex

    rows, temporal = dataframe_rows(df)
    url = publish("SQL Lab export", [Tab("Results", rows, temporal)], email, now)
    return url, len(df.index)


@api(id="sqllab_gsheets", name="SQL Lab Google Sheets Export")
class GSheetsExportAPI(RestApi):
    class_permission_name = "GoogleSheets"

    @expose("/export/", methods=("POST",))
    @protect()
    @permission_name("export")
    def export_gsheets(self) -> Response:
        """Export SQL query results to Google Sheets."""
        return json_export_endpoint(self, self._export_gsheets)

    def _export_gsheets(self, body: dict[str, Any]) -> Response:
        export_request = parse_sql_export_request(body)
        url, row_count = export_sql(
            export_request,
            g.user.email,
            datetime.now(timezone.utc),
        )
        return self.response(200, spreadsheet_url=url, row_count=row_count)
