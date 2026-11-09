"""One-off OAuth flow that prints GSHEETS_CREDENTIALS and a fresh export folder id."""

from __future__ import annotations

import argparse
import pprint
import sys

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .auth import USER_SCOPES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "client_secret_json", help="OAuth desktop client JSON from Google Cloud"
    )
    parser.add_argument("--folder-name", default="Superset exports")
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(
        args.client_secret_json,
        scopes=USER_SCOPES,
        autogenerate_code_verifier=True,
    )
    creds = flow.run_local_server(port=0, prompt="consent")
    if not creds.refresh_token:
        sys.exit(
            "No refresh token returned. Revoke the app at "
            "https://myaccount.google.com/permissions and re-run."
        )

    # drive.file only sees folders the app created, so the folder is made here.
    folder = (
        build("drive", "v3", credentials=creds)
        .files()
        .create(
            body={
                "name": args.folder_name,
                "mimeType": "application/vnd.google-apps.folder",
            },
            fields="id",
        )
        .execute()
    )
    credentials = {
        "type": "authorized_user",
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
    }
    print(
        "WARNING: the output below is a secret; do not commit or share it.",
        file=sys.stderr,
    )
    print(f"GSHEETS_CREDENTIALS = {pprint.pformat(credentials)}")
    print(f'GSHEETS_FOLDER_ID = "{folder["id"]}"')
