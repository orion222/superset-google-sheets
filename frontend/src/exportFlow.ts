import { toasts } from '@apache-superset/core';

const DEFAULT_MESSAGE = 'Export failed';
const SHEETS_LINK_LABEL = 'Open in Google Sheets';

interface ExportResult {
  spreadsheet_url: string;
  message: string;
}

export async function runExport(
  pendingMessage: string,
  request: () => Promise<ExportResult>,
): Promise<void> {
  const pendingId = toasts.addInfoToast(pendingMessage, { duration: 0 });

  try {
    const { spreadsheet_url, message } = await request();
    if (!spreadsheet_url) {
      throw new Error(DEFAULT_MESSAGE);
    }
    toasts.addSuccessToast(message, {
      duration: 0,
      link: { href: spreadsheet_url, label: SHEETS_LINK_LABEL },
    });
  } catch (error) {
    toasts.addDangerToast(
      error instanceof Error && error.message ? error.message : DEFAULT_MESSAGE,
    );
  } finally {
    toasts.removeToast(pendingId);
  }
}
