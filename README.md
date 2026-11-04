# superset-google-sheets
An extension of https://github.com/michael-s-molina/superset-extensions/tree/main/sqllab-gsheets

Adds **Export to Google Sheets** to Superset's chart download menus and to SQL Lab. Each export creates a new spreadsheet in a Google Shared Drive folder and shares it with the exporting user.

## Requirements

- A Superset build that includes the `superset_core.exports` API and the frontend `toasts` API and download-menu locations. No released Superset includes these yet.
- The `apache-superset-core>=0.1.0` floor in `backend/pyproject.toml` is a placeholder. Raise it to the first release that ships them.
- A Google Cloud service account with the Google Sheets API and Google Drive API enabled, and a Shared Drive (see below).

## Supported menus

The **Export to Google Sheets** item appears in:

- Explore: **Export All Data**, plus an **Export pivoted to Google Sheets** entry for pivot tables.
- The standalone and embedded chart download control.
- A dashboard chart's **Download** menu, plus the pivoted entry for pivot tables.
- **Drill to Detail**.
- **Drill By**.
- SQL Lab's editor menu.

The items appear only for users with the `can export on GoogleSheets` permission. The frontend registers them after `GET /extensions/michael-s-molina/sqllab-gsheets/chart/capabilities` succeeds, so other users see none.

## What gets exported

- The same rows and columns as **Export to .CSV**, with one tab per query.
- Cells are typed:
  - numbers stay numbers;
  - dates become real dates, formatted `yyyy-mm-dd hh:mm:ss`;
  - empty values stay blank;
  - integers above 2^53 become exact text;
  - text is never interpreted as a formula.
- Chart spreadsheets are titled `<chart name> · YYYY-MM-DD HH:mm UTC`. An unsaved chart is titled "Untitled chart".
- SQL Lab spreadsheets are titled `SQL Lab export · YYYY-MM-DD HH:mm UTC`, with a single `Results` tab.
- The spreadsheet is shared with the user's Superset account email as an editor.

## Installation

1. Bundle the extension from the repository root:

   ```bash
   superset-extensions bundle
   ```

2. Copy the generated `.supx` file to your Superset extensions directory (configured via `EXTENSIONS_PATH` in `superset_config.py`):

   ```python
   EXTENSIONS_PATH = "/path/to/extensions"
   ```

3. Restart Superset. The extension loads automatically.

For more details, see the [deployment documentation](https://superset.apache.org/developer_portal/extensions/deployment).

## Configuration

Add these keys to `superset_config.py`:

| Key | Default |
|---|---|
| `GSHEETS_SERVICE_ACCOUNT` | required |
| `GSHEETS_SHARED_DRIVE_FOLDER_ID` | required |
| `GSHEETS_EXPORT_SYNC_MAX_ROWS` | `100_000` |
| `GSHEETS_EXPORT_TIMEOUT_SECONDS` | `900` (used by dashboard exports, coming in 1.2.0) |

```python
GSHEETS_SERVICE_ACCOUNT = {
    "type": "service_account",
    "project_id": "your-project-id",
    "private_key_id": "your-private-key-id",
    "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
    "client_email": "your-service-account@your-project-id.iam.gserviceaccount.com",
    "client_id": "123456789",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}
GSHEETS_SHARED_DRIVE_FOLDER_ID = "your-shared-drive-folder-id"
```

### Setting up the service account

1. Go to [Google Cloud Console](https://console.cloud.google.com).
2. Create a project, or select an existing one.
3. Enable the **Google Sheets API** and **Google Drive API**.
4. Go to **APIs & Services > Credentials**.
5. Click **Create Credentials > Service Account**, fill in the details, and click **Create**.
6. Open the service account, go to the **Keys** tab, and click **Add Key > Create new key > JSON**.
7. Copy the JSON file's contents into `GSHEETS_SERVICE_ACCOUNT`.

## Shared Drive setup

Exports are created in a Shared Drive folder, not in the service account's own Drive. Set it up before enabling the extension:

1. Create a Shared Drive.
2. Add the service account as a **Content manager**.
3. Limit membership to the service account and break-glass admins only. **Never add a team group.**
4. Turn on **Allow people who aren't shared drive members to access files**, so the users each export is shared with can open it.
5. Set a retention policy so old exports are removed.
6. Set `GSHEETS_SHARED_DRIVE_FOLDER_ID` to the ID of the Drive or of a folder in it.

Why no team group: every Drive member sees every export. That bypasses Superset's row-level security and dataset permissions, which only apply to the user who ran the export.

## Permissions

- Grant `can export on GoogleSheets` to each role that should export. It is granted to no role by default.
- Users also need `can_csv`, or `can_export_data` when `GRANULAR_EXPORT_CONTROLS` is on.
- Users need an email address on their Superset account, because the spreadsheet is shared with it.

**Upgrading from 1.0.0:** SQL Lab export used to require only `read`. It now requires all of the above, so grant them to the roles that already export from SQL Lab.

## Limits

A single-chart export with more than `GSHEETS_EXPORT_SYNC_MAX_ROWS` rows (counted across all its queries) is refused with a "Too large for Google Sheets…" message before any file is created. Use **Export to .CSV** for those.

## Development

Backend tests:

```bash
python3.11 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -e "backend[test]"
pip install -e /path/to/superset/superset-core
pytest backend/tests
```

Frontend tests:

```bash
cd frontend && npm install && npm test
```

The upstream README is kept in `ORIGINAL_README.md`.
