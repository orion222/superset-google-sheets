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
import { ApiError, getJson } from './client';
import { pollTask, TaskPollingCancelled, TaskResult } from './taskPolling';

jest.mock('./client', () => ({
  ...jest.requireActual('./client'),
  getJson: jest.fn(),
}));
const get = jest.mocked(getJson);
const TASK_ID = '07b24a60-50af-46ed-8a33-a8742d5ba601';
const result = (status: TaskResult['status']) => ({ result: { status } });

/** Changes the browser visibility state and fires its real lifecycle event. */
function setHidden(hidden: boolean): void {
  Object.defineProperty(document, 'hidden', {
    configurable: true,
    value: hidden,
  });
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  jest.useFakeTimers();
  get.mockReset();
  setHidden(false);
});

afterEach(() => {
  jest.useRealTimers();
});

test('polls core task details every five seconds until success and removes timers', async () => {
  get
    .mockResolvedValueOnce(result('pending'))
    .mockResolvedValueOnce(result('in_progress'))
    .mockResolvedValueOnce({
      result: {
        status: 'success',
        payload: { url: 'https://docs.google.com/sheets/test' },
      },
    });
  const watching = pollTask(TASK_ID);
  await jest.advanceTimersByTimeAsync(0);
  expect(get).toHaveBeenCalledWith(`/api/v1/task/${TASK_ID}`);
  await jest.advanceTimersByTimeAsync(4_999);
  expect(get).toHaveBeenCalledTimes(1);
  await jest.advanceTimersByTimeAsync(5_001);
  await expect(watching).resolves.toMatchObject({
    status: 'success',
    payload: { url: expect.any(String) },
  });
  expect(get).toHaveBeenCalledTimes(3);
  expect(jest.getTimerCount()).toBe(0);
});

test('pauses while hidden and resumes immediately when the page becomes visible', async () => {
  setHidden(true);
  get.mockResolvedValue(result('success'));
  const watching = pollTask(TASK_ID);
  await jest.advanceTimersByTimeAsync(60_000);
  expect(get).not.toHaveBeenCalled();
  setHidden(false);
  await expect(watching).resolves.toMatchObject({ status: 'success' });
  expect(get).toHaveBeenCalledTimes(1);
});

test('hiding during the five-second wait stops requests until visibility resumes', async () => {
  get
    .mockResolvedValueOnce(result('pending'))
    .mockResolvedValueOnce(result('success'));
  const watching = pollTask(TASK_ID);
  await jest.advanceTimersByTimeAsync(0);
  setHidden(true);
  await jest.advanceTimersByTimeAsync(60_000);
  expect(get).toHaveBeenCalledTimes(1);
  setHidden(false);
  await expect(watching).resolves.toMatchObject({ status: 'success' });
  expect(jest.getTimerCount()).toBe(0);
});

test('backs off transient errors to fifteen and thirty seconds and resets after recovery', async () => {
  get
    .mockRejectedValueOnce(new TypeError('Network failed'))
    .mockRejectedValueOnce(new ApiError('server details', 503))
    .mockResolvedValueOnce(result('in_progress'))
    .mockResolvedValueOnce(result('success'));
  const watching = pollTask(TASK_ID);
  await jest.advanceTimersByTimeAsync(0);
  await jest.advanceTimersByTimeAsync(14_999);
  expect(get).toHaveBeenCalledTimes(1);
  await jest.advanceTimersByTimeAsync(1);
  expect(get).toHaveBeenCalledTimes(2);
  await jest.advanceTimersByTimeAsync(29_999);
  expect(get).toHaveBeenCalledTimes(2);
  await jest.advanceTimersByTimeAsync(1);
  expect(get).toHaveBeenCalledTimes(3);
  await jest.advanceTimersByTimeAsync(5_000);
  await expect(watching).resolves.toMatchObject({ status: 'success' });
});

test.each([401, 403, 404])(
  'does not retry HTTP %s permission or missing-task failures',
  async status => {
    get.mockRejectedValue(new ApiError('private details', status));
    const watching = pollTask(TASK_ID);
    const rejected = expect(watching).rejects.toBeInstanceOf(ApiError);
    await jest.advanceTimersByTimeAsync(0);
    await rejected;
    expect(get).toHaveBeenCalledTimes(1);
    expect(jest.getTimerCount()).toBe(0);
  },
);

test.each(['failure', 'aborted', 'timed_out'] as const)(
  'stops on terminal %s tasks',
  async status => {
    get.mockResolvedValue(result(status));
    const watching = pollTask(TASK_ID);
    await jest.advanceTimersByTimeAsync(0);
    await expect(watching).resolves.toEqual({ status });
    expect(get).toHaveBeenCalledTimes(1);
    expect(jest.getTimerCount()).toBe(0);
  },
);

test('rejects malformed task responses without polling indefinitely', async () => {
  get.mockResolvedValue({ result: { status: 'unknown' } });
  const watching = pollTask(TASK_ID);
  const rejected = expect(watching).rejects.toThrow('Invalid task response');
  await jest.advanceTimersByTimeAsync(0);
  await rejected;
  expect(jest.getTimerCount()).toBe(0);
});

test('cancellation removes the hidden-page listener and stops future polling', async () => {
  setHidden(true);
  const remove = jest.spyOn(document, 'removeEventListener');
  const controller = new AbortController();
  const watching = pollTask(TASK_ID, { signal: controller.signal });
  const rejected =
    expect(watching).rejects.toBeInstanceOf(TaskPollingCancelled);
  controller.abort();
  await rejected;
  expect(remove).toHaveBeenCalledWith('visibilitychange', expect.any(Function));
  expect(jest.getTimerCount()).toBe(0);
  setHidden(false);
  await jest.advanceTimersByTimeAsync(60_000);
  expect(get).not.toHaveBeenCalled();
  remove.mockRestore();
});

test('cancels an unresolved request without waiting for the HTTP response', async () => {
  let resolve!: (value: unknown) => void;
  get.mockImplementationOnce(
    () =>
      new Promise(done => {
        resolve = done;
      }),
  );
  const controller = new AbortController();
  const watching = pollTask(TASK_ID, { signal: controller.signal });
  const rejected =
    expect(watching).rejects.toBeInstanceOf(TaskPollingCancelled);
  await jest.advanceTimersByTimeAsync(0);
  controller.abort();
  await rejected;
  expect(jest.getTimerCount()).toBe(0);
  resolve(result('success'));
  await jest.advanceTimersByTimeAsync(60_000);
  expect(get).toHaveBeenCalledTimes(1);
});

test('a finite wait budget also expires while the page is hidden', async () => {
  setHidden(true);
  const watching = pollTask(TASK_ID, { timeoutMs: 10_000 });
  const rejected = expect(watching).rejects.toThrow('Task polling timed out');
  await jest.advanceTimersByTimeAsync(10_000);
  await rejected;
  expect(jest.getTimerCount()).toBe(0);
});

test('a slow response never causes overlapping polling requests', async () => {
  let complete!: (value: unknown) => void;
  get
    .mockImplementationOnce(
      () =>
        new Promise(resolve => {
          complete = resolve;
        }),
    )
    .mockResolvedValueOnce(result('success'));
  const watching = pollTask(TASK_ID);
  await jest.advanceTimersByTimeAsync(10_000);
  expect(get).toHaveBeenCalledTimes(1);
  complete(result('pending'));
  await jest.advanceTimersByTimeAsync(5_000);
  await expect(watching).resolves.toMatchObject({ status: 'success' });
  expect(get).toHaveBeenCalledTimes(2);
});

test.each([408, 429])('retries recoverable HTTP %s responses', async status => {
  get
    .mockRejectedValueOnce(new ApiError('Try later', status))
    .mockResolvedValueOnce(result('success'));
  const watching = pollTask(TASK_ID);
  await jest.advanceTimersByTimeAsync(15_000);
  await expect(watching).resolves.toMatchObject({ status: 'success' });
  expect(get).toHaveBeenCalledTimes(2);
});

test('healthy polling slows to fifteen seconds after a minute and thirty after five minutes', async () => {
  get.mockResolvedValue(result('pending'));
  const controller = new AbortController();
  const watching = pollTask(TASK_ID, { signal: controller.signal });
  const rejected =
    expect(watching).rejects.toBeInstanceOf(TaskPollingCancelled);
  await jest.advanceTimersByTimeAsync(60_000);
  expect(get).toHaveBeenCalledTimes(13);
  await jest.advanceTimersByTimeAsync(14_999);
  expect(get).toHaveBeenCalledTimes(13);
  await jest.advanceTimersByTimeAsync(1);
  expect(get).toHaveBeenCalledTimes(14);
  await jest.advanceTimersByTimeAsync(225_000);
  const requestsAtFiveMinutes = get.mock.calls.length;
  await jest.advanceTimersByTimeAsync(29_999);
  expect(get).toHaveBeenCalledTimes(requestsAtFiveMinutes);
  await jest.advanceTimersByTimeAsync(1);
  expect(get).toHaveBeenCalledTimes(requestsAtFiveMinutes + 1);
  controller.abort();
  await rejected;
  expect(jest.getTimerCount()).toBe(0);
});
