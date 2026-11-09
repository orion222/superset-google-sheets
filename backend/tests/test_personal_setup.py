from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock


def _load_setup_module(monkeypatch):
    oauthlib = ModuleType("google_auth_oauthlib")
    oauthlib.__path__ = []
    flow_module = ModuleType("google_auth_oauthlib.flow")
    flow_module.InstalledAppFlow = type("InstalledAppFlow", (), {})
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", oauthlib)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_module)

    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "gsheets_personal_setup.py"
    )
    spec = importlib.util.spec_from_file_location("gsheets_personal_setup_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return sys.modules[module.main.__module__]


def test_personal_setup_explicitly_enables_pkce(monkeypatch):
    setup = _load_setup_module(monkeypatch)
    flow = MagicMock()
    flow.run_local_server.return_value = SimpleNamespace(
        refresh_token="refresh",
        client_id="client-id",
        client_secret="client-secret",
    )
    from_client_secrets_file = MagicMock(return_value=flow)
    monkeypatch.setattr(
        setup.InstalledAppFlow,
        "from_client_secrets_file",
        from_client_secrets_file,
        raising=False,
    )
    drive = MagicMock()
    drive.files().create().execute.return_value = {"id": "folder-1"}
    monkeypatch.setattr(setup, "build", MagicMock(return_value=drive))
    monkeypatch.setattr(sys, "argv", ["gsheets-personal-setup", "client-secret.json"])

    setup.main()

    from_client_secrets_file.assert_called_once_with(
        "client-secret.json",
        scopes=["https://www.googleapis.com/auth/drive.file"],
        autogenerate_code_verifier=True,
    )
