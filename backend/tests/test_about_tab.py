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
"""Metadata tab tests without Google API calls."""

from datetime import datetime, timedelta, timezone

from michael_s_molina.sqllab_gsheets.about_tab import (
    ExportedTab,
    SkippedChart,
    build_about_tab,
)
from superset_core.exports.types import AppliedFilter, DashboardExportInfo


def test_about_contains_export_state_and_tab_chart_mapping() -> None:
    info = DashboardExportInfo(
        "Revenue",
        "https://superset.test/dashboard/permalink/filter-snapshot/",
        [AppliedFilter("Region", "EU"), AppliedFilter("Category", "=literal text")],
        {7: "https://superset.test/explore/?slice_id=7"},
    )
    tab = build_about_tab(
        info,
        datetime(2026, 1, 1, 12, 30, tzinfo=timezone(timedelta(hours=2))),
        [ExportedTab("7 - Sales", "Sales", info.chart_urls[7], 42)],
        [SkippedChart(8, "Broken", "The chart could not be exported.")],
    )

    assert tab.name == "About"
    assert tab.temporal_columns == []
    assert all(len(row) == 4 for row in tab.rows)
    assert ["Dashboard", "Revenue", "", ""] in tab.rows
    assert ["Dashboard link", info.permalink_url, "", ""] in tab.rows
    assert ["Exported at (UTC)", "2026-01-01 10:30:00 UTC", "", ""] in tab.rows
    assert ["Region", "EU", "", ""] in tab.rows
    assert ["Category", "=literal text", "", ""] in tab.rows
    assert ["7 - Sales", "Sales", info.chart_urls[7], 42] in tab.rows
    assert ["Broken", "The chart could not be exported.", "", ""] in tab.rows


def test_about_empty_sections_are_explicit_and_time_defaults_to_utc() -> None:
    tab = build_about_tab(
        DashboardExportInfo("Empty", "/dashboard/1/", [], {}),
        datetime(2026, 1, 1),
        [],
        [],
    )
    assert ["Exported at (UTC)", "2026-01-01 00:00:00 UTC", "", ""] in tab.rows
    assert tab.rows.count(["None", "", "", ""]) == 2
    assert all(len(row) == 4 for row in tab.rows)
