# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Preserve filter snapshots and isolate each dashboard export's identity."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from michael_s_molina.sqllab_gsheets.errors import INVALID_REQUEST, ExportRejected
from michael_s_molina.sqllab_gsheets.filter_hash import (
    dashboard_export_key,
    snapshot_filters,
)


def test_snapshot_keeps_query_and_display_state_without_aliasing() -> None:
    """Queued work must not change when the caller mutates a nested selection."""
    source: dict[str, Any] = {
        "region": {
            "extraFormData": {
                "filters": [{"col": "region", "op": "IN", "val": ["CA"]}]
            },
            "filterState": {"value": ["CA"], "label": "California"},
            "ownState": {"search": "transient"},
        }
    }
    original = deepcopy(source)
    snapshot = snapshot_filters(source)
    assert snapshot == {
        "region": {
            "extraFormData": original["region"]["extraFormData"],
            "filterState": original["region"]["filterState"],
        }
    }
    assert source == original
    source["region"]["extraFormData"]["filters"][0]["val"].append("NY")
    source["region"]["filterState"]["value"].append("NY")
    assert snapshot["region"]["extraFormData"]["filters"][0]["val"] == ["CA"]
    assert snapshot["region"]["filterState"]["value"] == ["CA"]


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        "filters",
        {1: {}},
        {"f": None},
        {"f": []},
        {"f": {"extraFormData": []}},
        {"f": {"filterState": None}},
        {"f": {"extraFormData": {"v": {1, 2}}}},
        {"f": {"filterState": {"value": float("nan")}}},
        {"f": {"extraFormData": {"value": float("inf")}}},
    ],
)
def test_snapshot_rejects_malformed_or_non_json_state(value: Any) -> None:
    """Invalid filter state is a safe client error before task submission."""
    with pytest.raises(ExportRejected) as error:
        snapshot_filters(value)
    assert (error.value.status, error.value.message) == (400, INVALID_REQUEST)


def test_snapshot_rejects_recursive_state() -> None:
    """Recursive Python input must not leak a serialization failure."""
    recursive: dict[str, Any] = {}
    recursive["value"] = recursive
    with pytest.raises(ExportRejected) as error:
        snapshot_filters({"f": {"filterState": recursive}})
    assert error.value.status == 400


def test_equivalent_filter_maps_share_a_key_despite_ui_changes() -> None:
    """Dictionary ordering and transient labels do not create duplicate exports."""
    left = snapshot_filters(
        {
            "region": {
                "extraFormData": {
                    "filters": [{"col": "region", "op": "IN", "val": ["CA"]}]
                },
                "filterState": {"value": ["CA"], "label": "California"},
            },
            "time": {"extraFormData": {"time_range": "Last year"}},
        }
    )
    right = snapshot_filters(
        {
            "time": {"extraFormData": {"time_range": "Last year"}},
            "region": {
                "filterState": {
                    "label": "CA",
                    "value": ["CA"],
                    "validateMessage": "transient",
                },
                "extraFormData": {
                    "filters": [{"val": ["CA"], "op": "IN", "col": "region"}]
                },
                "ownState": {"search": "CA"},
            },
        }
    )
    original = deepcopy(left)
    assert dashboard_export_key(7, left, 17) == dashboard_export_key(7, right, 17)
    assert left == original


def test_keys_separate_users_dashboards_and_effective_filters() -> None:
    """A task can be joined only for the same requester and requested data."""
    selected = snapshot_filters(
        {
            "f": {
                "extraFormData": {
                    "filters": [{"col": "region", "op": "IN", "val": ["CA"]}]
                },
                "filterState": {"value": ["CA"]},
            }
        }
    )
    changed_query = deepcopy(selected)
    changed_query["f"]["extraFormData"]["filters"][0]["val"] = ["NY"]
    changed_value = deepcopy(selected)
    changed_value["f"]["filterState"]["value"] = ["NY"]
    keys = {
        dashboard_export_key(7, selected, 17),
        dashboard_export_key(7, selected, 18),
        dashboard_export_key(8, selected, 17),
        dashboard_export_key(7, changed_query, 17),
        dashboard_export_key(7, changed_value, 17),
    }
    assert len(keys) == 5


def test_explicitly_cleared_filter_does_not_join_saved_defaults() -> None:
    """An absent mask can apply defaults whereas an explicit empty entry clears them."""
    assert dashboard_export_key(7, {}, 17) != dashboard_export_key(
        7, snapshot_filters({"f": {"extraFormData": {}}}), 17
    )
