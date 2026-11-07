# superset-google-sheets
An extension of https://github.com/michael-s-molina/superset-extensions/tree/main/sqllab-gsheets

Adds **Export to Google Sheets** to Superset's chart and dashboard download menus and to SQL Lab. Each export creates a new spreadsheet in a Google Drive folder and shares it with the exporting user, unless the exporter is the personal Google account that owns the file. The folder lives in a Shared Drive (service account) or in a personal Google account's Drive.

## Requirements

- A Superset build that includes the `superset_core.exports` API and the frontend `toasts` API and download-menu locations. No released Superset includes these yet.
- The `apache-superset-core>=0.1.0` floor in `backend/pyproject.toml` is a placeholder. Raise it to the first release that ships them.
- The Google Sheets API and Google Drive API enabled in a Google Cloud project.
- Either a service account plus a Shared Drive, or a personal Google account (see Configuration).

## Supported menus

The **Export to Google Sheets** item appears in:

- Explore: **Export All Data**, plus an **Export pivoted to Google Sheets** entry for pivot tables.
- The standalone and embedded chart download control.
- A dashboard chart's **Download** menu, plus the pivoted entry for pivot tables.
- **Drill to Detail**.
- **Drill By**.
- SQL Lab's editor menu.
- The dashboard's main **Download** menu, when background tasks are enabled.

Who sees the items:

- **Hidden** for users without the `can export on GoogleSheets` permission or without an email address on their Superset account. The frontend registers the items only after `GET /extensions/michael-s-molina/sqllab-gsheets/chart/capabilities` succeeds and reports `can_export`.
- **Disabled** in the chart menus for users without `can_csv` (or `can_export_data` when `GRANULAR_EXPORT_CONTROLS` is on), like Superset's own download items.
- The SQL Lab item is never disabled. For a user who lacks a required permission, the export fails with a permission error.
- The dashboard item also needs `can read on Task` and background tasks. Its host menu disables downloads when dashboard export is not permitted.

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
- The spreadsheet is shared with the user's Superset account email as an editor, unless that email is the personal Google account that owns the file.

## Dashboard exports

Open the dashboard's main **Download** menu and choose **Export to Google Sheets**. The export runs in a background task, and a completion toast provides the spreadsheet link. The sheet contains chart query data in dashboard layout order, with one tab per query and an **About** tab containing the dashboard permalink, export time, applied native filters, chart links and skipped-chart reasons.

The active native filters are copied when the export starts. Each filter retains its chart scope, and cleared filters stay cleared. Superset's dashboard export API applies native filters; cross-filters are excluded. Queries run as the requesting Superset user, with access and row-level security checked in the worker. Data comes from saved dashboard charts; results retain the host export API's query limits.

Repeated clicks with the same user, dashboard and filter values join the running task. Different filter values start a separate export. After a task finishes, another click starts a new export. Browser polling starts every five seconds, slows to 15 seconds after one minute and 30 seconds after five minutes, backs off after transient failures, and pauses while the browser tab is hidden. After 30 minutes, the browser stops waiting and directs the user to the task list. Closing the page also stops browser polling; the task remains available in Superset's task list.

### Worker setup

Enable the Global Task Framework in `superset_config.py`:

```python
FEATURE_FLAGS = {
    "GLOBAL_TASK_FRAMEWORK": True,
}
GSHEETS_EXPORT_TIMEOUT_SECONDS = 900
```

Configure the broker, result/cache backend and Celery worker according to [Superset's task documentation](https://superset.apache.org/developer_portal/extensions/tasks). Install the same extension bundle and Google credentials/folder configuration on both the web server and worker, then restart both. The dashboard command is hidden when the task feature is disabled; enabling the feature does not verify that a worker is online.

Users need the extension and data export permissions below, dashboard export permission and access to that dashboard, plus `can read on Task` to observe the result. The worker rechecks the user's export and dashboard permissions before reading data. Tasks are private to the requester.

### Limits and failures

The dashboard budget includes chart header rows and the About tab within the extension's 10,000,000-cell budget. Charts that fail or would exceed the remaining budget are skipped as a whole and recorded in About. If every chart is skipped, the task fails without creating a spreadsheet. The synchronous row cap applies to individual chart and SQL Lab exports; dashboard exports use the shared cell budget and worker timeout.

Use Superset's task list to cancel an export. Cancellation and timeout are cooperative: the worker checks between charts and Google write phases, then trashes a known incomplete spreadsheet. Google errors also trigger cleanup, and sharing occurs only after all tabs are written. Cancellation arriving after writing completes can leave a completed spreadsheet URL in a cancelled task; inspect its result before retrying. A hard-stopped worker or an unresolved file-creation response can leave a partial file for operational cleanup. Reopening the page does not automatically resume completion toasts; the task list retains the result.

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

### Deploying under an application path

The extension supports Superset at the domain root or under a configured path such as `/superset`. API requests use the `common.application_root` value Superset renders in the page's bootstrap data. Frontend chunks load relative to the extension's remote-entry script, preserving the path supplied by Superset's extension loader. No separate extension URL setting is required.

## Configuration

Add these keys to `superset_config.py`:

| Key | Default |
|---|---|
| `GSHEETS_CREDENTIALS` | required. `"type"` is `"service_account"` or `"authorized_user"` |
| `GSHEETS_FOLDER_ID` | required. The Shared Drive folder (service account) or the script-created folder (personal account) |
| `GSHEETS_EXPORT_SYNC_MAX_ROWS` | `100_000` |
| `GSHEETS_EXPORT_TIMEOUT_SECONDS` | `900` (positive integer; dashboard task timeout in seconds) |

Pick one credential option.

### Option A: service account + Shared Drive

```python
GSHEETS_CREDENTIALS = {
    "type": "service_account",
    "project_id": "your-project-id",
    "private_key_id": "your-private-key-id",
    "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
    "client_email": "your-service-account@your-project-id.iam.gserviceaccount.com",
    "client_id": "123456789",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}
GSHEETS_FOLDER_ID = "your-shared-drive-folder-id"
```

#### Setting up the service account

1. Go to [Google Cloud Console](https://console.cloud.google.com).
2. Create a project, or select an existing one.
3. Enable the **Google Sheets API** and **Google Drive API**.
4. Go to **APIs & Services > Credentials**.
5. Click **Create Credentials > Service Account**, fill in the details, and click **Create**.
6. Open the service account, go to the **Keys** tab, and click **Add Key > Create new key > JSON**.
7. Copy the JSON file's contents into `GSHEETS_CREDENTIALS`.

Then set up the Shared Drive (next section).

### Option B: personal Google account

Exports are owned by one personal Google account. No Shared Drive is needed.

1. In [Google Cloud Console](https://console.cloud.google.com), enable the **Google Sheets API** and **Google Drive API**.
2. Configure the OAuth consent screen:
   - User type: **External**.
   - Add the `drive.file` scope.
   - Set the publishing status to **In production**. Under **Testing**, refresh tokens expire after 7 days.
   - An unverified app in production with only `drive.file` needs no verification. Consent may show an "unverified app" screen once, which the owner clicks through.
3. Create an OAuth client of type **Desktop app** and download its JSON.
4. Install the setup dependencies and run the script from the repository root. It opens a browser for consent, creates the export folder, and prints the config:

   ```bash
   pip install -e "backend[setup]"
   python scripts/gsheets_personal_setup.py path/to/client_secret.json
   ```

   Use `--folder-name NAME` to name the folder. The default is `Superset exports`.

5. Paste the printed lines into `superset_config.py`:

   ```python
   GSHEETS_CREDENTIALS = {'client_id': '...', 'client_secret': '...', 'refresh_token': '...', 'type': 'authorized_user'}
   GSHEETS_FOLDER_ID = "..."
   ```

The output contains a refresh token. Treat it as a secret and do not commit it.

Caveats:

- One Google account owns every export and pays for its storage.
- Sharing comes from the owner's account, so teammates' share emails come from the owner.
- The token is revoked if unused for 6 months, or if the owner revokes access. Past about 100 refresh tokens per client, the oldest are dropped, so re-running the script many times invalidates old configs.
- Keep the folder the script created. A hand-made folder won't work, because `drive.file` only sees folders the app created.
- Set your Superset email to your Google account's email, or exports get shared to yourself. When the exporter's email matches the owner's (ignoring case), the share is skipped.
- The owner sees every teammate's export. This is the same trust model as Shared Drive membership.

## Shared Drive setup

This applies to Option A only. Exports are created in a Shared Drive folder, not in the service account's own Drive. Set it up before enabling the extension:

1. Create a Shared Drive.
2. Add the service account as a **Content manager**.
3. Limit membership to the service account and break-glass admins only. **Never add a team group.**
4. Turn on **Allow people who aren't shared drive members to access files**, so the users each export is shared with can open it.
5. Set a retention policy so old exports are removed.
6. Set `GSHEETS_FOLDER_ID` to the ID of the Drive or of a folder in it. Open the Drive or folder in Google Drive; the ID is the last path segment of its URL. For `https://drive.google.com/drive/folders/0ABcDeFgHiJkLmN`, the ID is `0ABcDeFgHiJkLmN`.

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

**Upgrading from 1.0.0:**

- SQL Lab export used to require only `read`. It now requires all of the above, so grant them to the roles that already export from SQL Lab.
- Two config keys were renamed: `GSHEETS_SERVICE_ACCOUNT` is now `GSHEETS_CREDENTIALS`, and `GSHEETS_SHARED_DRIVE_FOLDER_ID` is now `GSHEETS_FOLDER_ID`. The service account JSON itself is unchanged. If only the old keys are set, the Superset server log names the rename; the user sees only a generic export error.

## Export retries

Each export creates its spreadsheet once, with a private request marker. If Google returns a server error or loses the response, the extension searches for that marked spreadsheet and continues only when it finds exactly one match. An empty, incomplete, or failed search never causes another creation request. If recovery cannot identify the file, the export fails and logs its marker for investigation; an unreturned file can remain in Drive. Separate chart and SQL Lab export clicks create separate spreadsheets; matching dashboard clicks join an active task.

Fixed-range value writes, formatting and cleanup retain bounded retries. Tab creation and sharing requests are attempted once; a subsequent failure triggers cleanup of the known spreadsheet. The authenticated HTTP transport also disables implicit retries and redirects, so a lost response cannot silently replay a creation request below the export code.

## Limits

Individual chart and SQL Lab exports are refused with a "Too large for Google Sheets…" message before any file is created. Use **Export to .CSV** for them. Dashboard exports skip charts that exceed the remaining budget.

- A chart export with more than `GSHEETS_EXPORT_SYNC_MAX_ROWS` rows, counted across all its queries.
- A SQL Lab export with more than `GSHEETS_EXPORT_SYNC_MAX_ROWS` rows, or with `SQL_MAX_ROW` rows or more when Superset's `SQL_MAX_ROW` is set. A result that reaches `SQL_MAX_ROW` may have been cut short by Superset, so it is refused rather than exported truncated. With both at their default of 100,000, the most a SQL Lab export takes is 99,999 rows.
- Any export over the extension's budget of 10,000,000 cells per spreadsheet, counted as rows (including the header row) times columns, summed over tabs.

The extension keeps a conservative 10,000,000-cell budget. See [Google's documentation](https://support.google.com/drive/answer/37603?hl=en) for the service's published limits.

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
