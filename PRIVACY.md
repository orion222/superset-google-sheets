# Privacy Policy

The superset-google-sheets extension exports data from an Apache Superset instance to Google Sheets.

## What the app accesses

With the `drive.file` scope, the app can access only the files and folders it creates itself. It cannot see or change any other files in your Google Drive.

## What the app does with your data

- It creates spreadsheets and an export folder in the Google Drive account that authorized it.
- It writes the chart or query results that a Superset user chose to export into those spreadsheets.
- It shares each spreadsheet with the email address of the Superset user who exported it.

## What the app does not do

- It stores no data outside Google Drive and the Superset instance that runs it.
- It sends no data to the developer or to any third party.
- It does not use your data for advertising, analytics, or model training.

## Credentials

The OAuth refresh token stays in the Superset server's configuration, under the control of whoever operates that server.

## Revoking access

You can revoke the app's access at any time at https://myaccount.google.com/permissions. Spreadsheets it already created stay in your Drive until you delete them.

## Contact

Open an issue at https://github.com/orion222/superset-google-sheets/issues.
