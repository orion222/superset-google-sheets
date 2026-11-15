from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol

import numpy as np
import pandas as pd

CellValue = str | int | float | bool


class ChartQueryResult(Protocol):
    colnames: Sequence[str]
    coltypes: Sequence[Any]
    data: Iterable[Mapping[str, Any]]


_TEMPORAL_TYPE = object()

# Sheets stores numbers as doubles, so integers beyond 2**53 would silently lose digits.
_MAX_SAFE_INT = 2**53
_SHEETS_EPOCH = datetime(1899, 12, 30)
_UNIX_EPOCH = datetime(1970, 1, 1)


def _serial(moment: datetime) -> float:
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return (moment - _SHEETS_EPOCH) / timedelta(days=1)


def _is_temporal_type(coltype: Any) -> bool:
    return coltype is _TEMPORAL_TYPE or getattr(coltype, "name", None) == "TEMPORAL"


def to_cell(value: Any, coltype: Any | None) -> CellValue:
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
        if _is_temporal_type(coltype):
            return _serial(_UNIX_EPOCH + timedelta(milliseconds=value))
        return value if abs(value) <= _MAX_SAFE_INT else str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if math.isfinite(value) and _is_temporal_type(coltype):
            return _serial(_UNIX_EPOCH + timedelta(milliseconds=value))
        return value if math.isfinite(value) else str(value)
    return value if isinstance(value, str) else str(value)


def _rows(
    colnames: Sequence[str],
    coltypes: Sequence[Any],
    records: Iterable[Mapping[str, Any] | Sequence[Any]],
) -> tuple[list[list[CellValue]], list[int]]:
    rows: list[list[CellValue]] = [list(colnames)]
    temporal = [
        index for index, coltype in enumerate(coltypes) if _is_temporal_type(coltype)
    ]
    for record in records:
        values = (
            (record.get(name) for name in colnames)
            if isinstance(record, Mapping)
            else record
        )
        rows.append(
            [to_cell(value, coltype) for value, coltype in zip(values, coltypes)]
        )
    return rows, temporal


def query_result_data(
    result: ChartQueryResult,
) -> tuple[list[list[CellValue]], list[int]]:
    return _rows(result.colnames, result.coltypes, result.data)


def query_rows(result: ChartQueryResult) -> list[list[CellValue]]:
    """Return rows using the original chart-export helper contract."""
    return query_result_data(result)[0]


def temporal_columns(result: ChartQueryResult) -> list[int]:
    return [
        index for index, coltype in enumerate(result.coltypes) if _is_temporal_type(coltype)
    ]


_TEMPORAL_INFERRED = {"date", "datetime", "datetime64"}


def _is_temporal(column: pd.Series) -> bool:
    # pyarrow's integer_object_nulls frames hold dates as objects, not datetime64.
    if pd.api.types.is_datetime64_any_dtype(column.dtype):
        return True
    return column.dtype == object and (
        pd.api.types.infer_dtype(column, skipna=True) in _TEMPORAL_INFERRED
    )


def dataframe_rows(df: pd.DataFrame) -> tuple[list[list[CellValue]], list[int]]:
    """Header-first rows plus the indexes of date columns, for SQL Lab results."""
    coltypes = [
        _TEMPORAL_TYPE if _is_temporal(df.iloc[:, index]) else None
        for index in range(df.shape[1])
    ]
    return _rows(
        [str(name) for name in df.columns],
        coltypes,
        df.itertuples(index=False, name=None),
    )
