"""Shared authorization, size checks, and delivery for Google Sheets exports."""

from datetime import datetime

from superset_core.exports.export import can_export_data

from .errors import ExportRejected, GENERIC_FAILURE
from .google_sheets import (
    check_cell_limit,
    create_spreadsheet,
    GoogleExportError,
    Tab,
)


def authorize_export(email: str | None) -> str:
    if not email or not can_export_user(email):
        raise ExportRejected(
            403, "You don't have permission to export to Google Sheets."
        )
    return email


def can_export_user(email: str | None) -> bool:
    return bool(email) and can_export_data()


def reject_too_many_rows(row_count: int, limit: int) -> None:
    if row_count > limit:
        raise ExportRejected(
            400,
            f"Too large for Google Sheets (more than {limit} rows). "
            "Use Export to .CSV instead.",
        )


def deliver_export(stem: str, tabs: list[Tab], email: str, now: datetime) -> str:
    check_cell_limit(tabs)
    title = f"{stem} · {now.strftime('%Y-%m-%d %H:%M')} UTC"
    try:
        return create_spreadsheet(title, tabs, email)
    except GoogleExportError as ex:
        raise ExportRejected(500, GENERIC_FAILURE) from ex
