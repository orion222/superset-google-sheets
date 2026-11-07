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

"""Validate filter snapshots and identify equivalent dashboard exports."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .errors import ExportRejected, INVALID_REQUEST


def snapshot_filters(value: Any) -> dict[str, Any]:
    """Copy query and display state, discarding transient filter UI metadata."""
    if not isinstance(value, dict):
        raise ExportRejected(400, INVALID_REQUEST)
    snapshot: dict[str, Any] = {}
    for key, entry in value.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            raise ExportRejected(400, INVALID_REQUEST)
        filtered: dict[str, Any] = {}
        for field in ("extraFormData", "filterState"):
            if field in entry:
                if not isinstance(entry[field], dict):
                    raise ExportRejected(400, INVALID_REQUEST)
                filtered[field] = entry[field]
        snapshot[key] = filtered
    try:
        return json.loads(json.dumps(snapshot, allow_nan=False))
    except (TypeError, ValueError, RecursionError) as ex:
        raise ExportRejected(400, INVALID_REQUEST) from ex


def dashboard_export_key(
    dashboard_id: int, active_data_mask: dict[str, Any], user_id: int
) -> str:
    """Hash the requester, dashboard and query-relevant filter values."""
    effective = {}
    for key, entry in active_data_mask.items():
        filtered = dict(entry)
        if "filterState" in filtered:
            state = filtered["filterState"]
            filtered["filterState"] = (
                {"value": state["value"]} if "value" in state else {}
            )
        effective[key] = filtered
    encoded = json.dumps(
        ["gsheets-dashboard-v1", user_id, dashboard_id, effective],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
