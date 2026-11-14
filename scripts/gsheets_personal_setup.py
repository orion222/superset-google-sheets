"""Authorize Superset to export to a folder in the selected Google account."""

import argparse
import json
import os
import pprint
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _open_private_file(path: str, flags: int) -> int:
    return os.open(path, flags, mode=0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "client_secret_json", help="OAuth desktop client JSON from Google Cloud"
    )
    parser.add_argument("--folder-name", default="Superset exports")
    parser.add_argument(
        "--config-output",
        type=Path,
        help="Write the Superset configuration to a new JSON file instead of stdout",
    )
    args = parser.parse_args()
    if args.config_output:
        args.config_output = args.config_output.expanduser()
        if args.config_output.exists():
            parser.error("--config-output already exists")
        if not args.config_output.parent.is_dir():
            parser.error("--config-output parent directory does not exist")

    flow = InstalledAppFlow.from_client_secrets_file(
        args.client_secret_json, scopes=SCOPES
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
    if args.config_output:
        with open(
            args.config_output, "x", encoding="utf-8", opener=_open_private_file
        ) as output:
            json.dump(
                {
                    "GSHEETS_CREDENTIALS": credentials,
                    "GSHEETS_FOLDER_ID": folder["id"],
                },
                output,
            )
        print(f"Saved Google export configuration to {args.config_output}.")
        return

    print(
        "WARNING: the output below is a secret; do not commit or share it.",
        file=sys.stderr,
    )
    print(f"GSHEETS_CREDENTIALS = {pprint.pformat(credentials)}")
    print(f'GSHEETS_FOLDER_ID = "{folder["id"]}"')


if __name__ == "__main__":
    main()
