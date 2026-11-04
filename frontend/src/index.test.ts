import type * as Core from '@apache-superset/core';

type CoreModule = Pick<typeof Core, 'commands' | 'menus'>;

const fetchMock = jest.fn();
global.fetch = fetchMock as unknown as typeof fetch;

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
const err = (status: number, body: unknown) => ({
  ok: false,
  status,
  json: async () => body,
});
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
