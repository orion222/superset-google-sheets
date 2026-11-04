from __future__ import annotations

import pytest
import superset_core.rest_api.decorators as _decorators
from flask import Flask


# Outside a running Superset the real decorator raises NotImplementedError; this must
# be in place before any sqllab_gsheets module is imported.
def _passthrough_api(**_kwargs):
    return lambda cls: cls


_decorators.api = _passthrough_api


@pytest.fixture
def app():
    flask_app = Flask(__name__)
    flask_app.config.update(
        GSHEETS_SERVICE_ACCOUNT={"type": "service_account"},
        GSHEETS_SHARED_DRIVE_FOLDER_ID="folder-1",
    )
    with flask_app.app_context():
        yield flask_app
