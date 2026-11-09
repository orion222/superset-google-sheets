import { toasts } from '@apache-superset/core';
import { exportChart } from './chartExport';
import { err, mockFetch, ok } from '../test/httpMock';

const fetchMock = mockFetch();

beforeEach(() => {
  jest.clearAllMocks();
});

test('posts the payload and shows a success toast with the link', async () => {
  fetchMock.mockResolvedValue(ok({ spreadsheet_url: 'https://docs.google.com/spreadsheets/d/f/edit' }));
  await exportChart({ getPayload: async () => ({ queries: [] }), chartName: 'Sales', canDownload: true });
  expect(fetchMock).toHaveBeenCalledWith(
    '/extensions/michael-s-molina/sqllab-gsheets/chart/',
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

test('pivoted export overrides result_type', async () => {
  fetchMock.mockResolvedValue(ok({ spreadsheet_url: 'u' }));
  const payload = { result_type: 'full' };
  await exportChart(
    { getPayload: async () => payload, chartName: 'P', canDownload: true },
    'post_processed',
  );
  const requestBody = fetchMock.mock.calls[0]?.[1]?.body;
  expect(typeof requestBody).toBe('string');
  expect(JSON.parse(requestBody as string).payload.result_type).toBe('post_processed');
  expect(payload.result_type).toBe('full');
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
