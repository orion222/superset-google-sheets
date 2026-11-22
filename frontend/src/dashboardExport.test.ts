/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
import { toasts } from '@apache-superset/core';
import type { DashboardDownloadContext } from '@apache-superset/core';
import { postJson } from './client';
import { exportDashboard } from './dashboardExport';
import { pollTask, TaskPollingCancelled, TaskResult } from './taskPolling';

jest.mock('./client', () => ({
  ...jest.requireActual('./client'),
  postJson: jest.fn(),
}));
jest.mock('./taskPolling', () => ({
  ...jest.requireActual('./taskPolling'),
  pollTask: jest.fn(),
}));
const post = jest.mocked(postJson);
const poll = jest.mocked(pollTask);
const TASK_ID = '07b24a60-50af-46ed-8a33-a8742d5ba601';
const URL = 'https://docs.google.com/spreadsheets/d/test/edit';
const ctx: DashboardDownloadContext = {
  dashboardId: 42,
  dashboardTitle: 'Sales',
  canDownload: true,
  activeDataMask: {
    'NATIVE_FILTER-1': {
      extraFormData: { filters: [{ col: 'country', op: 'IN', val: ['CA'] }] },
      filterState: { value: ['CA'], label: 'Canada' },
    },
  },
};

beforeEach(() => {
  jest.clearAllMocks();
  post
    .mockReset()
    .mockResolvedValue({ task_id: TASK_ID, already_running: false });
  poll
    .mockReset()
    .mockResolvedValue({ status: 'success', payload: { url: URL } });
});

test('submits dashboard filters and opens the successful task result through a toast', async () => {
  jest.mocked(toasts.addInfoToast)
    .mockReturnValueOnce('preparing-id')
    .mockReturnValueOnce('watching-id');
  await exportDashboard(ctx);
  expect(post).toHaveBeenCalledWith('/dashboard/', {
    dashboardId: 42,
    activeDataMask: ctx.activeDataMask,
  });
  expect(poll).toHaveBeenCalledWith(TASK_ID, {
    signal: expect.any(AbortSignal),
  });
  expect(toasts.addSuccessToast).toHaveBeenCalledWith(
    'Dashboard sent to Google Sheets',
    {
      duration: 0,
      link: { href: URL, label: 'Open in Google Sheets' },
    },
  );
  expect(toasts.removeToast).toHaveBeenNthCalledWith(1, 'preparing-id');
  expect(toasts.removeToast).toHaveBeenNthCalledWith(2, 'watching-id');
  expect(toasts.removeToast).toHaveBeenCalledTimes(2);
  expect(toasts.addDangerToast).not.toHaveBeenCalled();
  expect(toasts.addInfoToast).toHaveBeenLastCalledWith(
    'Dashboard export started…',
    { duration: 0 },
  );
});

test('submission errors show a generic message and always dismiss the pending toast', async () => {
  post.mockRejectedValue(new Error('secret database details'));
  await exportDashboard(ctx);
  expect(toasts.addDangerToast).toHaveBeenCalledWith(
    'Dashboard export failed. Please try again.',
  );
  expect(toasts.removeToast).toHaveBeenCalledWith('pending-id');
  expect(poll).not.toHaveBeenCalled();
});

test('shows the public worker failure message without a success toast', async () => {
  const message = 'No dashboard charts could be exported to Google Sheets.';
  poll.mockResolvedValue({
    status: 'failure',
    payload: { error_message: message },
  });
  await exportDashboard(ctx);
  expect(toasts.addDangerToast).toHaveBeenCalledWith(message);
  expect(toasts.addSuccessToast).not.toHaveBeenCalled();
});

test.each([
  { status: 'failure' },
  { status: 'failure', payload: { error_message: ['malformed detail'] } },
  { status: 'timed_out' },
] satisfies TaskResult[])(
  'falls back to a generic error for $status without a usable message',
  async task => {
    poll.mockResolvedValue(task);
    await exportDashboard(ctx);
    expect(toasts.addDangerToast).toHaveBeenCalledWith(
      'Dashboard export failed. Please try again.',
    );
    expect(toasts.addSuccessToast).not.toHaveBeenCalled();
    expect(toasts.removeToast).toHaveBeenCalledWith('pending-id');
  },
);

test('does not report a task cancelled from the task list as a failure', async () => {
  poll.mockResolvedValue({
    status: 'aborted',
    payload: {
      error_message: 'The dashboard export was cancelled or timed out.',
    },
  });
  await exportDashboard(ctx);
  expect(toasts.addDangerToast).not.toHaveBeenCalled();
  expect(toasts.addSuccessToast).not.toHaveBeenCalled();
  expect(toasts.removeToast).toHaveBeenCalledTimes(2);
});

test('success without a URL is treated as a failure', async () => {
  poll.mockResolvedValue({ status: 'success', payload: {} });
  await exportDashboard(ctx);
  expect(toasts.addDangerToast).toHaveBeenCalledTimes(1);
  expect(toasts.addDangerToast).toHaveBeenCalledWith(
    'Could not check dashboard export status. View it in the task list.',
  );
  expect(toasts.addSuccessToast).not.toHaveBeenCalled();
});

test('polling errors never expose internal response details', async () => {
  poll.mockRejectedValue(new Error('private task exception and credentials'));
  await exportDashboard(ctx);
  expect(toasts.addDangerToast).toHaveBeenCalledWith(
    'Could not check dashboard export status. View it in the task list.',
  );
  expect(toasts.removeToast).toHaveBeenCalledWith('pending-id');
});

test('repeated responses for the same UUID share one watcher and one completion toast', async () => {
  let complete!: (task: TaskResult) => void;
  poll.mockImplementationOnce(
    () =>
      new Promise(resolve => {
        complete = resolve;
      }),
  );
  post.mockResolvedValue({ task_id: TASK_ID, already_running: true });
  const first = exportDashboard(ctx);
  const second = exportDashboard(ctx);
  await Promise.resolve();
  expect(poll).toHaveBeenCalledTimes(1);
  complete({ status: 'success', payload: { url: URL } });
  await Promise.all([first, second]);
  expect(toasts.addSuccessToast).toHaveBeenCalledTimes(1);
  expect(toasts.removeToast).toHaveBeenCalledTimes(3);
  expect(toasts.addInfoToast).toHaveBeenCalledWith(
    'Dashboard export already in progress…',
    { duration: 0 },
  );
});

test('pagehide cancels the watcher and dismisses its pending toast without an error', async () => {
  poll.mockImplementationOnce(
    (_id, options) =>
      new Promise((_resolve, reject) => {
        options?.signal?.addEventListener('abort', () =>
          reject(new TaskPollingCancelled()),
        );
      }),
  );
  const exporting = exportDashboard(ctx);
  await Promise.resolve();
  window.dispatchEvent(new Event('pagehide'));
  await exporting;
  expect(toasts.removeToast).toHaveBeenCalledWith('pending-id');
  expect(toasts.addDangerToast).not.toHaveBeenCalled();
  expect(toasts.addSuccessToast).not.toHaveBeenCalled();
});

test('invalid task IDs are refused before polling', async () => {
  post.mockResolvedValue({ task_id: '../other', already_running: false });
  await exportDashboard(ctx);
  expect(poll).not.toHaveBeenCalled();
  expect(toasts.addDangerToast).toHaveBeenCalledTimes(1);
});

test('leaving during submission does not start a watcher when the response arrives', async () => {
  let complete!: (value: unknown) => void;
  post.mockImplementationOnce(
    () =>
      new Promise(resolve => {
        complete = resolve;
      }),
  );
  const exporting = exportDashboard(ctx);
  window.dispatchEvent(new Event('pagehide'));
  complete({ task_id: TASK_ID, already_running: false });
  await exporting;
  expect(poll).not.toHaveBeenCalled();
  expect(toasts.addDangerToast).not.toHaveBeenCalled();
  expect(toasts.removeToast).toHaveBeenCalledWith('pending-id');
});

test('a context without download permission does not submit an export', async () => {
  await exportDashboard({ ...ctx, canDownload: false });
  expect(post).not.toHaveBeenCalled();
  expect(toasts.addInfoToast).not.toHaveBeenCalled();
  expect(toasts.addDangerToast).toHaveBeenCalledWith(
    "You don't have permission to export this dashboard.",
  );
});
