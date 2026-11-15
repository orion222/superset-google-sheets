import { err, mockFetch, ok } from '../test/httpMock';
import { registerIfCapable } from './registration';

const fetchMock = mockFetch();

beforeEach(() => {
  jest.clearAllMocks();
});

afterEach(() => {
  jest.restoreAllMocks();
});

test('registers at an arbitrary capability path when export is allowed', async () => {
  fetchMock.mockResolvedValue(ok({ can_export: true }));
  const register = jest.fn();

  await registerIfCapable('/dashboard/capabilities', register);

  expect(fetchMock).toHaveBeenCalledWith(
    '/extensions/michael-s-molina/sqllab-gsheets/dashboard/capabilities',
    { method: 'GET' },
  );
  expect(register).toHaveBeenCalledTimes(1);
});

test('does not register when export is disabled', async () => {
  fetchMock.mockResolvedValue(ok({ can_export: false }));
  const register = jest.fn();

  await registerIfCapable('/dashboard/capabilities', register);

  expect(register).not.toHaveBeenCalled();
});

test('logs unexpected capability failures', async () => {
  fetchMock.mockResolvedValue(err(500, { message: 'server error' }));
  const warn = jest.spyOn(console, 'warn').mockImplementation();

  await registerIfCapable('/dashboard/capabilities', jest.fn());

  expect(warn).toHaveBeenCalledWith(
    'Extension capability check failed',
    '/dashboard/capabilities',
    expect.objectContaining({ status: 500 }),
  );
});

test('logs menu registration failures', async () => {
  fetchMock.mockResolvedValue(ok({ can_export: true }));
  const error = new Error('registry unavailable');
  const register = jest.fn(() => {
    throw error;
  });
  const warn = jest.spyOn(console, 'warn').mockImplementation();

  await registerIfCapable('/dashboard/capabilities', register);

  expect(warn).toHaveBeenCalledWith(
    'Extension menu registration failed',
    '/dashboard/capabilities',
    error,
  );
});

test('keeps permission-denied capability checks quiet', async () => {
  fetchMock.mockResolvedValue(err(403, { message: 'forbidden' }));
  const warn = jest.spyOn(console, 'warn').mockImplementation();

  await registerIfCapable('/dashboard/capabilities', jest.fn());

  expect(warn).not.toHaveBeenCalled();
});
