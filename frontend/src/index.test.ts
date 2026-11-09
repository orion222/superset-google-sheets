import type * as Core from '@apache-superset/core';
import { err, mockFetch, ok } from '../test/httpMock';

type CoreModule = Pick<typeof Core, 'commands' | 'menus' | 'sqlLab' | 'toasts'>;

const fetchMock = mockFetch();
const flushPromises = () => new Promise<void>(resolve => setTimeout(resolve, 0));

// Each isolated registry gets its own mock instance, so the spies are read from inside it.
async function loadExtension(): Promise<CoreModule> {
  let core!: CoreModule;
  await jest.isolateModulesAsync(async () => {
    core = await import('@apache-superset/core');
    await import('./index');
  });
  await flushPromises();
  return core;
}

test('registers nothing when capabilities is 403', async () => {
  fetchMock.mockResolvedValue(err(403, {}));
  const { commands, menus } = await loadExtension();
  expect(menus.registerMenuItem).not.toHaveBeenCalled();
  expect(commands.registerCommand).not.toHaveBeenCalled();
});

test('logs an unexpected capabilities failure', async () => {
  const warn = jest.spyOn(console, 'warn').mockImplementation();
  fetchMock.mockResolvedValue(err(500, { message: 'server error' }));
  try {
    const { commands, menus } = await loadExtension();
    expect(menus.registerMenuItem).not.toHaveBeenCalled();
    expect(commands.registerCommand).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledWith(
      'Extension capability check failed',
      '/chart/capabilities',
      expect.objectContaining({ status: 500 }),
    );
  } finally {
    warn.mockRestore();
  }
});

test('registers nothing when can_export is false', async () => {
  fetchMock.mockResolvedValue(ok({ can_export: false }));
  const { commands, menus } = await loadExtension();
  expect(menus.registerMenuItem).not.toHaveBeenCalled();
  expect(commands.registerCommand).not.toHaveBeenCalled();
});

test('registers chart items at every chart location, both pivoted locations to the pivoted command', async () => {
  fetchMock.mockResolvedValue(ok({ can_export: true }));
  const { commands, menus } = await loadExtension();
  const calls = (menus.registerMenuItem as jest.Mock).mock.calls.map(
    ([item, loc, group]) => [loc, item.command, group],
  );
  expect(calls).toEqual(
    expect.arrayContaining([
      ['explore.chart.download', 'sqllab_gsheets.exportChart', 'primary'],
      ['explore.chart.download.pivoted', 'sqllab_gsheets.exportChartPivoted', 'primary'],
      ['explore.standalone.download', 'sqllab_gsheets.exportChart', 'primary'],
      ['dashboard.chart.download', 'sqllab_gsheets.exportChart', 'primary'],
      ['dashboard.chart.download.pivoted', 'sqllab_gsheets.exportChartPivoted', 'primary'],
      ['chart.drillDetail.download', 'sqllab_gsheets.exportChart', 'primary'],
      ['chart.drillBy.download', 'sqllab_gsheets.exportChart', 'primary'],
      ['sqllab.editor', 'sqllab_gsheets.export', 'secondary'],
    ]),
  );
  expect(calls).toHaveLength(8);
  const ids = (commands.registerCommand as jest.Mock).mock.calls.map(([c]) => c.id);
  expect(ids.sort()).toEqual([
    'sqllab_gsheets.export',
    'sqllab_gsheets.exportChart',
    'sqllab_gsheets.exportChartPivoted',
  ]);
});

test('SQL Lab export reports a missing spreadsheet URL as a failure', async () => {
  fetchMock
    .mockResolvedValueOnce(ok({ can_export: true }))
    .mockResolvedValueOnce(ok({ row_count: 3 }));
  const core = await loadExtension();
  (core.sqlLab.getCurrentTab as jest.Mock).mockReturnValue({
    getEditor: async () => ({ getValue: () => 'SELECT 1' }),
    databaseId: 7,
  });
  const callback = (core.commands.registerCommand as jest.Mock).mock.calls.find(
    ([command]) => command.id === 'sqllab_gsheets.export',
  )?.[1] as () => Promise<void>;

  await callback();

  expect(core.toasts.addSuccessToast).not.toHaveBeenCalled();
  expect(core.toasts.addDangerToast).toHaveBeenCalledWith('Export failed');
  expect(core.toasts.removeToast).toHaveBeenCalledWith('pending-id');
});

test('SQL Lab export shows the row count and spreadsheet link', async () => {
  fetchMock
    .mockResolvedValueOnce(ok({ can_export: true }))
    .mockResolvedValueOnce(
      ok({ spreadsheet_url: 'https://docs.google.com/spreadsheets/d/sql/edit', row_count: 3 }),
    );
  const core = await loadExtension();
  (core.sqlLab.getCurrentTab as jest.Mock).mockReturnValue({
    getEditor: async () => ({ getValue: () => 'SELECT 1' }),
    databaseId: 7,
  });
  const callback = (core.commands.registerCommand as jest.Mock).mock.calls.find(
    ([command]) => command.id === 'sqllab_gsheets.export',
  )?.[1] as () => Promise<void>;

  await callback();

  expect(core.toasts.addInfoToast).toHaveBeenCalledWith(
    'Exporting to Google Sheets...',
    { duration: 0 },
  );
  expect(core.toasts.addSuccessToast).toHaveBeenCalledWith(
    'Exported 3 rows to Google Sheets',
    {
      duration: 0,
      link: {
        href: 'https://docs.google.com/spreadsheets/d/sql/edit',
        label: 'Open in Google Sheets',
      },
    },
  );
  expect(core.toasts.removeToast).toHaveBeenCalledWith('pending-id');
});
