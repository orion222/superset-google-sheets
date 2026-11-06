from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from superset_core.exports.types import ChartQueryResult, GenericDataType

from michael_s_molina.sqllab_gsheets.cells import (
    dataframe_rows,
    query_rows,
    temporal_columns,
    to_cell,
)


@pytest.mark.parametrize(
    "value, coltype, expected",
    [
        (None, GenericDataType.STRING, ""),
        (pd.NaT, GenericDataType.TEMPORAL, ""),
        (float("nan"), GenericDataType.NUMERIC, ""),
        (True, GenericDataType.BOOLEAN, True),
        (42, GenericDataType.NUMERIC, 42),
        (2**53 + 1, GenericDataType.NUMERIC, str(2**53 + 1)),
        (-(2**53) - 1, GenericDataType.NUMERIC, str(-(2**53) - 1)),
        (1.5, GenericDataType.NUMERIC, 1.5),
        (datetime(1900, 1, 1), GenericDataType.TEMPORAL, 2.0),
        (datetime(2026, 1, 1, 12), GenericDataType.TEMPORAL, 46023.5),
        (
            datetime(2026, 1, 1, 12, tzinfo=timezone(timedelta(hours=2))),
            GenericDataType.TEMPORAL,
            46023 + 10 / 24,
        ),
        (date(2026, 1, 1), GenericDataType.TEMPORAL, 46023.0),
        (1767268800000, GenericDataType.TEMPORAL, 46023.5),  # epoch ms
        (1767268800000.0, GenericDataType.TEMPORAL, 46023.5),
        (np.float64(1767268800000), GenericDataType.TEMPORAL, 46023.5),
        (0.0, GenericDataType.TEMPORAL, 25569.0),
        (-43200000.0, GenericDataType.TEMPORAL, 25568.5),
        (0.5, GenericDataType.TEMPORAL, 25569 + 0.5 / 86400000),
        (float("nan"), GenericDataType.TEMPORAL, ""),
        (float("inf"), GenericDataType.TEMPORAL, "inf"),
        (float("-inf"), GenericDataType.TEMPORAL, "-inf"),
        (1767268800000.0, GenericDataType.NUMERIC, 1767268800000.0),
        (1767268800000.0, None, 1767268800000.0),
        ("=SUM(A1)", GenericDataType.STRING, "=SUM(A1)"),  # RAW input: never a formula
        (Decimal("1.25"), GenericDataType.NUMERIC, 1.25),
        (Decimal("9007199254740993"), GenericDataType.NUMERIC, "9007199254740993"),
        (Decimal("42"), GenericDataType.NUMERIC, 42),
        (pd.NA, GenericDataType.NUMERIC, ""),
        (Decimal("1E+5000"), GenericDataType.NUMERIC, "1" + "0" * 5000),
        (Decimal("-9007199254740993"), GenericDataType.NUMERIC, "-9007199254740993"),
        (Decimal("sNaN"), GenericDataType.NUMERIC, ""),
        (Decimal("NaN"), GenericDataType.NUMERIC, ""),
    ],
)
def test_to_cell(value, coltype, expected):
    assert to_cell(value, coltype) == expected


def test_to_cell_integral_decimal_becomes_plain_int():
    assert type(to_cell(Decimal("42"), GenericDataType.NUMERIC)) is int


def test_to_cell_numpy_integer_becomes_plain_int():
    cell = to_cell(np.int64(7), GenericDataType.NUMERIC)
    assert type(cell) is int and cell == 7


def test_to_cell_numpy_float_becomes_plain_float():
    cell = to_cell(np.float64(1.5), GenericDataType.NUMERIC)
    assert type(cell) is float and cell == 1.5


def test_to_cell_numpy_nan_becomes_empty_string():
    assert to_cell(np.float64("nan"), GenericDataType.NUMERIC) == ""


def test_to_cell_numpy_bool_stays_bool():
    assert to_cell(np.bool_(True), GenericDataType.BOOLEAN) is True


def test_to_cell_large_float_is_not_stringified():
    assert to_cell(1e300, GenericDataType.NUMERIC) == 1e300


def test_query_rows_header_then_values_in_column_order():
    result = ChartQueryResult(
        ["b", "a"],
        [GenericDataType.NUMERIC, GenericDataType.STRING],
        [{"a": "x", "b": 1}],
    )
    assert query_rows(result) == [["b", "a"], [1, "x"]]


def test_query_rows_nullable_epoch_milliseconds_stay_dates() -> None:
    """A null promotes epoch milliseconds to floats without changing their meaning."""
    df = pd.DataFrame({"d": [1767268800000, None], "n": [1.5, 2.5]})
    result = ChartQueryResult(
        ["d", "n"],
        [GenericDataType.TEMPORAL, GenericDataType.NUMERIC],
        [
            {"d": timestamp, "n": number}
            for timestamp, number in df.itertuples(index=False, name=None)
        ],
    )

    assert query_rows(result) == [["d", "n"], [46023.5, 1.5], ["", 2.5]]
    assert temporal_columns(result) == [0]


def test_temporal_columns():
    result = ChartQueryResult(
        ["d", "n"], [GenericDataType.TEMPORAL, GenericDataType.NUMERIC], []
    )
    assert temporal_columns(result) == [0]


def test_dataframe_rows_detects_datetime_columns():
    df = pd.DataFrame({"d": pd.to_datetime(["2026-01-01"]), "n": [1]})
    rows, temporal = dataframe_rows(df)
    assert rows == [["d", "n"], [46023.0, 1]] and temporal == [0]


def test_dataframe_rows_yield_plain_python_types():
    df = pd.DataFrame({"n": [1], "f": [float("nan")]})
    rows, _ = dataframe_rows(df)
    assert type(rows[1][0]) is int
    assert rows[1][1] == ""


def test_dataframe_rows_detects_pyarrow_date32_columns():
    pa = pytest.importorskip("pyarrow")
    table = pa.table(
        {
            "d": pa.array([date(2026, 1, 2), None], type=pa.date32()),
            "n": pa.array([1, None], type=pa.int64()),
        }
    )
    df = table.to_pandas(integer_object_nulls=True)
    rows, temporal = dataframe_rows(df)
    assert temporal == [0]
    assert rows[1][0] == 46024.0 and rows[2][0] == ""


def test_dataframe_rows_detects_object_date_and_datetime_columns():
    df = pd.DataFrame(
        {
            "d": pd.Series([date(2026, 1, 2), None], dtype=object),
            "dt": pd.Series([datetime(2026, 1, 2, 12), None], dtype=object),
            "s": pd.Series(["2026-01-02", None], dtype=object),
            "empty": pd.Series([None, None], dtype=object),
        }
    )
    rows, temporal = dataframe_rows(df)
    assert temporal == [0, 1]
    assert rows[1][:3] == [46024.0, 46024.5, "2026-01-02"]
