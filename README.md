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

Who sees the items:

- **Hidden** for users without the `can export on GoogleSheets` permission or without an email address on their Superset account. The frontend registers the items only after `GET /extensions/michael-s-molina/sqllab-gsheets/chart/capabilities` succeeds and reports `can_export`.
- **Disabled** in the chart menus for users without `can_csv` (or `can_export_data` when `GRANULAR_EXPORT_CONTROLS` is on), like Superset's own download items.
- The SQL Lab item is never disabled. For a user who lacks a required permission, the export fails with a permission error.

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
6. Set `GSHEETS_SHARED_DRIVE_FOLDER_ID` to the ID of the Drive or of a folder in it. Open the Drive or folder in Google Drive; the ID is the last path segment of its URL. For `https://drive.google.com/drive/folders/0ABcDeFgHiJkLmN`, the ID is `0ABcDeFgHiJkLmN`.

Why no team group: every Drive member sees every export. That bypasses Superset's row-level security and dataset permissions, which only apply to the user who ran the export.

## Permissions

Every export needs all of:

- `can export on GoogleSheets`.
- `can_csv`, or `can_export_data` when `GRANULAR_EXPORT_CONTROLS` is on.
- An email address on the user's Superset account, because the spreadsheet is shared with it.

A chart export runs the chart's query with Superset's own access checks, so it returns only data the user could already download from that chart.

A SQL Lab export also needs what running the same SQL in SQL Lab needs, and is refused with a permission error otherwise:

- `can execute sql query on SQLLab` (the `sql_lab` role grants it).
- A database with **Expose in SQL Lab** turned on.
- Access to every table the SQL reads. The check matches SQL Lab's: database access, or `datasource_access` on a registered dataset for each table.
- SQL without Jinja templating. SQL containing `{{`, `{%` or `{#` is refused, whether or not `ENABLE_TEMPLATE_PROCESSING` is on, because a template can render differently for the access check and for the query. Run templated queries in SQL Lab and use **Export to .CSV**.

### Default role grants

Superset's role sync, which runs on every `superset init`, grants `can export on GoogleSheets` to **Gamma** and **Alpha** (and Admin). With the checks above, a Gamma user can export only chart data they could already download, and SQL Lab results only if they also hold SQL Lab rights.

Removing the permission from Gamma or Alpha in the UI does not last: the role sync replaces those roles' permissions on the next `superset init`. To restrict export, either:

- give the users who shouldn't export a custom role, copied from Gamma or Alpha without `can export on GoogleSheets`, instead of the built-in role; or
- keep the permission out of the built-in roles with a custom security manager, then grant it to custom roles:

  ```python
  from superset.security import SupersetSecurityManager

  class SecurityManager(SupersetSecurityManager):
      ADMIN_ONLY_VIEW_MENUS = SupersetSecurityManager.ADMIN_ONLY_VIEW_MENUS | {
          "GoogleSheets"
      }

  CUSTOM_SECURITY_MANAGER = SecurityManager
  ```

**Upgrading from 1.0.0:** SQL Lab export used to require only `read`. It now requires all of the above, so grant them to the roles that already export from SQL Lab.

## Limits

These exports are refused with a "Too large for Google Sheets…" message before any file is created. Use **Export to .CSV** for them.

- A chart export with more than `GSHEETS_EXPORT_SYNC_MAX_ROWS` rows, counted across all its queries.
- A SQL Lab export with more than `GSHEETS_EXPORT_SYNC_MAX_ROWS` rows, or with `SQL_MAX_ROW` rows or more when Superset's `SQL_MAX_ROW` is set. A result that reaches `SQL_MAX_ROW` may have been cut short by Superset, so it is refused rather than exported truncated. With both at their default of 100,000, the most a SQL Lab export takes is 99,999 rows.
- Any export over Google's limit of 10,000,000 cells per spreadsheet, counted as rows (including the header row) times columns, summed over tabs.

## Development

### Running against a local Superset

Dev mode loads the extension straight from this folder, so you don't need to bundle a `.supx` file. You need a Superset checkout that includes the core APIs listed under [Requirements](#requirements), with its own virtualenv (`.venv` below).

1. Install the extension's Python dependencies into Superset's virtualenv. Superset doesn't install an extension's dependencies:

   ```bash
   /path/to/superset/.venv/bin/pip install "google-api-python-client>=2.0.0" "google-auth>=2.0.0"
   ```

2. Set up a service account and a Shared Drive as described in [Setting up the service account](#setting-up-the-service-account) and [Shared Drive setup](#shared-drive-setup).

3. Add the following to your `superset_config.py`. If you don't already have one, create it and point `SUPERSET_CONFIG_PATH` at it:

   ```python
   import json

   FEATURE_FLAGS = {"ENABLE_EXTENSIONS": True}
   LOCAL_EXTENSIONS = ["/path/to/superset-google-sheets"]

   with open("/path/to/service-account.json") as f:
       GSHEETS_SERVICE_ACCOUNT = json.load(f)
   GSHEETS_SHARED_DRIVE_FOLDER_ID = "your-shared-drive-folder-id"
   ```

4. Build the extension and rebuild it whenever a file changes. Superset reads `dist/`, so run this before starting Superset:

   ```bash
   cd /path/to/superset-google-sheets
   /path/to/superset/.venv/bin/superset-extensions dev
   ```

5. In a second terminal, start the Superset backend. `superset init` creates the `can export on GoogleSheets` permission:

   ```bash
   cd /path/to/superset
   .venv/bin/superset init
   .venv/bin/superset run -p 8088 --reload --debugger
   ```

6. In a third terminal, start the Superset frontend:

   ```bash
   cd /path/to/superset/superset-frontend
   npm run dev-server
   ```

7. Give your Superset user an email address under **Settings > List Users**. Without one, the menu items don't appear.

With Docker Compose instead: the dev config already turns on `ENABLE_EXTENSIONS` and loads `.supx` files from `docker/extensions/`. Run `superset-extensions bundle`, copy the `.supx` file there, install the two Google packages inside the container, and restart it.

### Manual checks

- Open a chart, choose **Download**, then **Export to Google Sheets**. A "Sending to Google Sheets…" toast appears, then a success toast with **Open in Google Sheets**. The file is in the Shared Drive folder and shared with your email.
- A pivot table also shows **Export pivoted to Google Sheets**.
- The item also appears in:
  - a dashboard chart's **Download** menu;
  - the standalone chart view (`?standalone=1`);
  - **Drill to Detail**;
  - **Drill By**;
  - SQL Lab's editor menu.
- These exports are refused:
  - SQL containing `{{ }}`: refused as templated SQL.
  - A chart over the row limit. Set `GSHEETS_EXPORT_SYNC_MAX_ROWS = 10` to try it with a small chart: refused as too large.
  - A SQL Lab export by a Gamma user without SQL Lab rights: refused with a permission error.
- Export a chart with about 100,000 rows and 20 columns, and check that it finishes within your web server's request timeout.

### Tests

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
