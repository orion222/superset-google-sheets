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

"""Share the data authorization checks between dashboard export entry points."""

from __future__ import annotations

from typing import Any, cast

from flask import current_app
from superset_core.exports.export import can_export_dashboard, can_export_data

from .errors import is_named_exception


def dashboard_export_allowed(
    email: str | None, dashboard_id: int | None = None
) -> bool:
    """Check the current Superset user, including dashboard access when known."""
    sm = cast(Any, current_app).appbuilder.sm
    if (
        not email
        or not sm.can_access("can_export", "GoogleSheets")
        or not can_export_data()
    ):
        return False
    if dashboard_id is None:
        return True
    try:
        return can_export_dashboard(dashboard_id)
    except Exception as ex:
        if is_named_exception(ex, "SupersetSecurityException"):
            return False
        raise
