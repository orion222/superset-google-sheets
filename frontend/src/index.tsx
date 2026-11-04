import { sqlLab, commands, menus, toasts } from '@apache-superset/core';
import { exportChart } from './chartExport';
import { EXTENSION_BASE, getJson, postJson } from './client';
import { ChartDownloadContext } from './coreTypes';

const CHART_LOCATIONS = [
  'explore.chart.download',
  'explore.standalone.download',
  'dashboard.chart.download',
  'chart.drillDetail.download',
  'chart.drillBy.download',
] as const;

const PIVOTED_CHART_LOCATIONS = [
  'explore.chart.download.pivoted',
  'dashboard.chart.download.pivoted',
] as const;

const SHEETS_LINK_LABEL = 'Open in Google Sheets';

async function exportSqlLab(): Promise<void> {
  const currentTab = sqlLab.getCurrentTab();

  if (!currentTab) {
    toasts.addWarningToast('No active tab found.');
    return;
  }

  const editor = await currentTab.getEditor();
  const sql = editor.getValue()?.trim();

  if (!sql) {
    toasts.addWarningToast('No SQL query to export. Please write a query first.');
    return;
  }

  if (!currentTab.databaseId) {
    toasts.addWarningToast('No database selected. Please select a database first.');
    return;
  }

  const pendingId = toasts.addInfoToast('Exporting to Google Sheets...', {
    duration: 0,
  });

  try {
    const data = await postJson<{ spreadsheet_url?: string; row_count: number }>(
      `${EXTENSION_BASE}/export/`,
      {
        sql,
        databaseId: currentTab.databaseId,
        catalog: currentTab.catalog,
        schema: currentTab.schema,
      },
    );
    if (data.spreadsheet_url) {
      toasts.addSuccessToast(`Exported ${data.row_count} rows to Google Sheets`, {
        duration: 0,
        link: { href: data.spreadsheet_url, label: SHEETS_LINK_LABEL },
      });
    }
  } catch (error) {
    toasts.addDangerToast(
      error instanceof Error && error.message ? error.message : 'Export failed',
    );
  } finally {
    toasts.removeToast(pendingId);
  }
}

function register(): void {
  commands.registerCommand(
    {
      id: 'sqllab_gsheets.export',
      title: 'Export to Google Sheets',
      description: 'Export current SQL query results to Google Sheets',
    },
    exportSqlLab,
  );
  commands.registerCommand(
    {
      id: 'sqllab_gsheets.exportChart',
      title: 'Export to Google Sheets',
      description: 'Export chart data to Google Sheets',
    },
    (ctx: ChartDownloadContext) => exportChart(ctx),
  );
  commands.registerCommand(
    {
      id: 'sqllab_gsheets.exportChartPivoted',
      title: 'Export pivoted to Google Sheets',
      description: 'Export pivoted chart data to Google Sheets',
    },
    (ctx: ChartDownloadContext) => exportChart(ctx, 'post_processed'),
  );

  menus.registerMenuItem(
    { view: 'builtin.editor', command: 'sqllab_gsheets.export' },
    'sqllab.editor',
    'secondary',
  );
  CHART_LOCATIONS.forEach(location =>
    menus.registerMenuItem(
      { view: location, command: 'sqllab_gsheets.exportChart' },
      location,
      'primary',
    ),
  );
  PIVOTED_CHART_LOCATIONS.forEach(location =>
    menus.registerMenuItem(
      { view: location, command: 'sqllab_gsheets.exportChartPivoted' },
      location,
      'primary',
    ),
  );
}

// Users without the extension permission get a 403 here and see none of the items.
getJson<{ can_export: boolean }>(`${EXTENSION_BASE}/chart/capabilities`)
  .then(({ can_export }) => {
    if (can_export) {
      register();
    }
  })
  .catch(() => undefined);
