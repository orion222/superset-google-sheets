import { toasts } from '@apache-superset/core';
import { exportChart } from './chartExport';
import { EXTENSION_BASE } from './client';

const fetchMock = jest.fn();
global.fetch = fetchMock as unknown as typeof fetch;

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
const err = (status: number, body: unknown) => ({
  ok: false,
  status,
  json: async () => body,
});

beforeEach(() => {
  jest.clearAllMocks();
});

test('posts the payload and shows a success toast with the link', async () => {
  fetchMock.mockResolvedValue(ok({ url: 'https://docs.google.com/spreadsheets/d/f/edit' }));
  await exportChart({ getPayload: async () => ({ queries: [] }), chartName: 'Sales', canDownload: true });
  expect(fetchMock).toHaveBeenCalledWith(
    `${EXTENSION_BASE}/chart/`,
    expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ payload: { queries: [] }, chartName: 'Sales' }),
    }),
  );
  expect(toasts.addInfoToast).toHaveBeenCalledWith('Sending to Google Sheets…', { duration: 0 });
  expect(toasts.addSuccessToast).toHaveBeenLastCalledWith('Sent to Google Sheets', {
    duration: 0,
    link: { href: 'https://docs.google.com/spreadsheets/d/f/edit', label: 'Open in Google Sheets' },
  });
  expect(toasts.removeToast).toHaveBeenCalledWith('pending-id');
});

test('posts chartId from the context', async () => {
  fetchMock.mockResolvedValue(ok({ url: 'u' }));
  await exportChart({ getPayload: async () => ({}), chartName: 'Sales', chartId: 59, canDownload: true });
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
    payload: {},
    chartName: 'Sales',
    chartId: 59,
  });
});

test('omits chartId when the context has none', async () => {
  fetchMock.mockResolvedValue(ok({ url: 'u' }));
  await exportChart({ getPayload: async () => ({}), chartName: 'Sales', canDownload: true });
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).not.toHaveProperty('chartId');
});

test('pivoted export overrides result_type', async () => {
  fetchMock.mockResolvedValue(ok({ url: 'u' }));
  await exportChart(
    { getPayload: async () => ({ result_type: 'full' }), chartName: 'P', canDownload: true },
    'post_processed',
  );
  expect(JSON.parse(fetchMock.mock.calls[0][1].body).payload.result_type).toBe('post_processed');
});

test('server message shown as a danger toast and the pending toast dismissed', async () => {
  const message = 'Too large for Google Sheets (2 rows, limit 1). Use Export to .CSV instead.';
  fetchMock.mockResolvedValue(err(400, { message }));
  await exportChart({ getPayload: async () => ({}), chartName: 'x', canDownload: true });
  expect(toasts.addDangerToast).toHaveBeenLastCalledWith(message);
  expect(toasts.addSuccessToast).not.toHaveBeenCalled();
  expect(toasts.removeToast).toHaveBeenCalledWith('pending-id');
});

test('falls back to a generic message when the error body has none', async () => {
  fetchMock.mockResolvedValue(err(500, {}));
  await exportChart({ getPayload: async () => ({}), chartName: 'x', canDownload: true });
  expect(toasts.addDangerToast).toHaveBeenLastCalledWith('Export failed');
});
