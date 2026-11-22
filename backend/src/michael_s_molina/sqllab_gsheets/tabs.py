from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .cells import CellValue
from .errors import ExportRejected

if TYPE_CHECKING:
    from superset_core.exports.types import ChartQueryResult

# Export cell budget shared by SQL Lab, chart and dashboard exports.
MAX_CELLS = 10_000_000


@dataclass(frozen=True)
class Tab:
    name: str
    rows: list[list[CellValue]]
    temporal_columns: list[int]


def grid_dimensions(rows: list[list[CellValue]]) -> tuple[int, int]:
    return max(1, len(rows)), max(1, max(map(len, rows), default=0))


def tab_cells(tab: Tab) -> int:
    """Count the rectangular grid allocated by Google Sheets, including headers."""
    rows, columns = grid_dimensions(tab.rows)
    return rows * columns


def query_cells(result: ChartQueryResult) -> int:
    """Count a query's grid before converting its rows; the first row is its header."""
    return (len(result.data) + 1) * max(1, len(result.colnames))


def check_cell_limit(tabs: list[Tab]) -> None:
    """Refuse tabs that would exceed Google's cell limit, before any Google call."""
    cells = sum(map(tab_cells, tabs))
    if cells > MAX_CELLS:
        raise ExportRejected(
            400,
            f"Too large for Google Sheets ({cells:,} cells, limit {MAX_CELLS:,}). "
            "Use Export to .CSV instead.",
        )
