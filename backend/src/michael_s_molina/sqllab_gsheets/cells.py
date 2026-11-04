from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from superset_core.exports.types import ChartQueryResult, GenericDataType

CellValue = str | int | float | bool

# Sheets stores numbers as doubles, so integers beyond 2**53 would silently lose digits.
_MAX_SAFE_INT = 2**53
_SHEETS_EPOCH = datetime(1899, 12, 30)
_UNIX_EPOCH = datetime(1970, 1, 1)


def _serial(moment: datetime) -> float:
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return (moment - _SHEETS_EPOCH) / timedelta(days=1)


def to_cell(value: Any, coltype: GenericDataType | None) -> CellValue:
    """Plain-Python cell for `value`, safe to JSON-encode; dates become Sheets serials."""
    if isinstance(value, np.datetime64):
        value = pd.Timestamp(value)
    elif isinstance(value, np.generic):
        value = value.item()

    if value is None or value is pd.NaT or value is pd.NA:
        return ""
    if isinstance(value, bool):  # before int: bool subclasses int
        return value
    if isinstance(value, datetime):
        return _serial(value)
    if isinstance(value, date):
        return _serial(datetime(value.year, value.month, value.day))
    if isinstance(value, Decimal):
        if value.is_snan():
            return ""
        if value.is_finite() and value == value.to_integral_value():
            # Formatted directly: int() and str() hit Python's digit limit on huge values.
            if abs(value) > _MAX_SAFE_INT:
                return format(value, "f")
            value = int(value)
        else:
            value = float(value)
    if isinstance(value, int):
        if coltype is GenericDataType.TEMPORAL:
            return _serial(_UNIX_EPOCH + timedelta(milliseconds=value))
        return value if abs(value) <= _MAX_SAFE_INT else str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return value if math.isfinite(value) else str(value)
    return value if isinstance(value, str) else str(value)


def query_rows(result: ChartQueryResult) -> list[list[CellValue]]:
    rows: list[list[CellValue]] = [list(result.colnames)]
    for record in result.data:
        rows.append(
            [
                to_cell(record.get(name), coltype)
                for name, coltype in zip(result.colnames, result.coltypes)
            ]
        )
    return rows


def temporal_columns(result: ChartQueryResult) -> list[int]:
    return [
        index
        for index, coltype in enumerate(result.coltypes)
        if coltype is GenericDataType.TEMPORAL
    ]


def dataframe_rows(df: pd.DataFrame) -> tuple[list[list[CellValue]], list[int]]:
    """Header-first rows plus the indexes of `datetime64` columns, for SQL Lab results."""
    temporal = [
        index
        for index, dtype in enumerate(df.dtypes)
        if pd.api.types.is_datetime64_any_dtype(dtype)
    ]
    rows: list[list[CellValue]] = [[str(name) for name in df.columns]]
    for record in df.itertuples(index=False, name=None):
        rows.append(
            [
                to_cell(cell, GenericDataType.TEMPORAL if i in temporal else None)
                for i, cell in enumerate(record)
            ]
        )
    return rows, temporal
