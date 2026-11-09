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
"""Build the leading metadata tab for a dashboard spreadsheet."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from superset_core.exports.types import DashboardExportInfo

from .cells import CellValue
from .google_sheets import Tab


@dataclass(frozen=True)
class ExportedTab:
    """A written tab and the chart whose query produced it."""

    name: str
    chart_name: str
    chart_url: str
    data_rows: int


@dataclass(frozen=True)
class SkippedChart:
    """A chart omitted from the export, with a consumer-safe reason."""

    chart_id: int
    chart_name: str
    reason: str


def build_about_tab(
    info: DashboardExportInfo,
    exported_at: datetime,
    exported_tabs: list[ExportedTab],
    skipped_charts: list[SkippedChart],
) -> Tab:
    """Describe the exact exported filter state, tab mapping and skipped charts."""
    timestamp = (
        exported_at.replace(tzinfo=timezone.utc)
        if exported_at.tzinfo is None
        else exported_at
    )
    rows: list[list[CellValue]] = [
        ["Dashboard export", "", "", ""],
        ["Dashboard", info.title, "", ""],
        ["Dashboard link", info.permalink_url, "", ""],
        [
            "Exported at (UTC)",
            timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "",
            "",
        ],
        ["", "", "", ""],
        ["Applied native filter", "Value", "", ""],
    ]
    rows.extend(
        [[item.name, item.display_value, "", ""] for item in info.applied_filters]
        or [["None", "", "", ""]]
    )
    rows.extend([["", "", "", ""], ["Sheet", "Chart", "Chart link", "Data rows"]])
    rows.extend(
        [
            [item.name, item.chart_name, item.chart_url, item.data_rows]
            for item in exported_tabs
        ]
    )
    rows.extend([["", "", "", ""], ["Skipped chart", "Reason", "", ""]])
    rows.extend(
        [[item.chart_name, item.reason, "", ""] for item in skipped_charts]
        or [["None", "", "", ""]]
    )
    return Tab("About", rows, [])
