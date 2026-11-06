# superset-google-sheets
An extension of https://github.com/michael-s-molina/superset-extensions/tree/main/sqllab-gsheets

## Export retries

Each export creates its spreadsheet once, with a private request marker. If Google returns a server error or loses the response, the extension searches for that marked spreadsheet and continues only when it finds exactly one match. An empty, incomplete, or failed search never causes another creation request. If recovery cannot identify the file, the export fails and logs its marker for investigation; an unreturned file can remain in Drive. Separate export clicks still create separate spreadsheets.

Fixed-range value writes, formatting and cleanup retain bounded retries. Tab creation and sharing requests are attempted once; a subsequent failure triggers cleanup of the known spreadsheet. The authenticated HTTP transport also disables implicit retries and redirects, so a lost response cannot silently replay a creation request below the export code.
