"""Credential factories and OAuth guidance for Google Sheets exports."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from google.auth.credentials import Credentials
from google.auth.exceptions import RefreshError
from google.oauth2 import credentials as user_credentials, service_account

SERVICE_ACCOUNT_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
USER_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
PERSONAL_ACCOUNT_TYPE = "authorized_user"


def _service_account(info: dict[str, Any]) -> Credentials:
    return service_account.Credentials.from_service_account_info(
        info, scopes=SERVICE_ACCOUNT_SCOPES
    )


def _personal_account(info: dict[str, Any]) -> Credentials:
    return user_credentials.Credentials.from_authorized_user_info(
        info, scopes=USER_SCOPES
    )


CREDENTIAL_FACTORIES: dict[str, Callable[[dict[str, Any]], Credentials]] = {
    "service_account": _service_account,
    PERSONAL_ACCOUNT_TYPE: _personal_account,
}


def credentials(info: dict[str, Any]) -> Credentials:
    """Build credentials using the factory and scopes registered for their type."""
    credential_type = info.get("type")
    if not isinstance(credential_type, str):
        raise ValueError(f"Unsupported Google credentials type: {credential_type}")
    factory = CREDENTIAL_FACTORIES.get(credential_type)
    if factory is None:
        raise ValueError(f"Unsupported Google credentials type: {credential_type}")
    return factory(info)


def failure_hint(error: Exception, info: dict[str, Any] | None) -> str:
    """Return setup guidance only for rejected personal-account refresh tokens."""
    if (
        isinstance(error, RefreshError)
        and info is not None
        and info.get("type") == PERSONAL_ACCOUNT_TYPE
    ):
        return (
            "; for a personal account, run gsheets-personal-setup to refresh credentials"
        )
    return ""
