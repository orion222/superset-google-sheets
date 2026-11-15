from __future__ import annotations

from dataclasses import dataclass

from .cells import CellValue
from .errors import ExportRejected

# Google's per-spreadsheet cell limit.
MAX_CELLS = 10_000_000


@dataclass(frozen=True)
class Tab:
    name: str
    rows: list[list[CellValue]]
    temporal_columns: list[int]


def check_cell_limit(tabs: list[Tab]) -> None:
    """Refuse tabs that would exceed Google's cell limit, before any Google call."""
    cells = sum(len(tab.rows) * len(tab.rows[0]) for tab in tabs if tab.rows)
    if cells > MAX_CELLS:
        raise ExportRejected(
            400,
            f"Too large for Google Sheets ({cells:,} cells, limit {MAX_CELLS:,}). "
            "Use Export to .CSV instead.",
        )
