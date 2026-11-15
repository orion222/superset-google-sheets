import { sqlLab, commands, menus, toasts } from '@apache-superset/core';
import type { ChartDownloadContext } from '@apache-superset/core';
import { exportChart } from './chartExport';
import { postJson } from './client';
import { runExport } from './exportFlow';
import { registerIfCapable } from './registration';

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

  await runExport('Exporting to Google Sheets...', async () => {
    const data = await postJson<{ spreadsheet_url: string; row_count: number }>(
      '/export/',
      {
        sql,
        databaseId: currentTab.databaseId,
        catalog: currentTab.catalog,
        schema: currentTab.schema,
      },
    );
    return {
      ...data,
      message: `Exported ${data.row_count} rows to Google Sheets`,
    };
  });
}

interface MenuRegistration {
  view: string;
  location: string;
  group: 'primary' | 'secondary';
}

// Chart menu locations pass this host context to callbacks, though the registry types it as `any`.
interface Contribution {
  command: { id: string; title: string; description: string };
  callback: (() => Promise<void>) | ((ctx: ChartDownloadContext) => Promise<void>);
  menuItems: MenuRegistration[];
}

const CONTRIBUTIONS: Contribution[] = [
  {
    command: {
      id: 'sqllab_gsheets.export',
      title: 'Export to Google Sheets',
      description: 'Export current SQL query results to Google Sheets',
    },
    callback: exportSqlLab,
    menuItems: [
      { view: 'builtin.editor', location: 'sqllab.editor', group: 'secondary' },
    ],
  },
  {
    command: {
      id: 'sqllab_gsheets.exportChart',
      title: 'Export to Google Sheets',
      description: 'Export chart data to Google Sheets',
    },
    callback: (ctx: ChartDownloadContext) => exportChart(ctx),
    menuItems: [
      {
        view: 'explore.chart.download',
        location: 'explore.chart.download',
        group: 'primary',
      },
      {
        view: 'explore.standalone.download',
        location: 'explore.standalone.download',
        group: 'primary',
      },
      {
        view: 'dashboard.chart.download',
        location: 'dashboard.chart.download',
        group: 'primary',
      },
      {
        view: 'chart.drillDetail.download',
        location: 'chart.drillDetail.download',
        group: 'primary',
      },
      {
        view: 'chart.drillBy.download',
        location: 'chart.drillBy.download',
        group: 'primary',
      },
    ],
  },
  {
    command: {
      id: 'sqllab_gsheets.exportChartPivoted',
      title: 'Export pivoted to Google Sheets',
      description: 'Export pivoted chart data to Google Sheets',
    },
    callback: (ctx: ChartDownloadContext) => exportChart(ctx, 'post_processed'),
    menuItems: [
      {
        view: 'explore.chart.download.pivoted',
        location: 'explore.chart.download.pivoted',
        group: 'primary',
      },
      {
        view: 'dashboard.chart.download.pivoted',
        location: 'dashboard.chart.download.pivoted',
        group: 'primary',
      },
    ],
  },
];

function register(): void {
  CONTRIBUTIONS.forEach(({ command, callback, menuItems }) => {
    commands.registerCommand(command, callback);
    menuItems.forEach(({ view, location, group }) =>
      menus.registerMenuItem({ view, command: command.id }, location, group),
    );
  });
}

// Users without the extension permission get a 403 here and see none of the items.
void registerIfCapable('/chart/capabilities', register);
