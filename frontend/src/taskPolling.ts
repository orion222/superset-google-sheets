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

export interface TaskResult {
  status:
    | 'pending'
    | 'in_progress'
    | 'aborting'
    | 'success'
    | 'failure'
    | 'aborted'
    | 'timed_out';
  payload?: { url?: unknown } | null;
}

export interface TaskPollingOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

const POLL_INTERVAL_MS = 5_000;
const MAX_WAIT_MS = 30 * 60 * 1_000;
const TASK_STATUSES: TaskResult['status'][] = [
  'pending',
  'in_progress',
  'aborting',
  'success',
  'failure',
  'aborted',
  'timed_out',
];

/** Distinguishes cancellation from failures that need an error toast. */
export class TaskPollingCancelled extends Error {
  constructor() {
    super('Task polling cancelled');
    Object.setPrototypeOf(this, new.target.prototype);
    this.name = 'AbortError';
  }
}

/** Waits between requests, stopping the timer while the page is hidden. */
function waitForPoll(delayMs: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const cleanup = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', onAbort);
      document.removeEventListener('visibilitychange', onVisibility);
    };
    const finish = () => {
      cleanup();
      resolve();
    };
    const onAbort = () => {
      cleanup();
      reject(new TaskPollingCancelled());
    };
    const onVisibility = () => {
      clearTimeout(timer);
      if (!document.hidden) {
        finish();
      }
    };
    signal.addEventListener('abort', onAbort, { once: true });
    document.addEventListener('visibilitychange', onVisibility);
    if (signal.aborted) {
      onAbort();
    } else if (!document.hidden) {
      timer = setTimeout(finish, delayMs);
    }
  });
}

/** Stops waiting for an in-flight response when its watcher is cancelled. */
function withCancellation<T>(
  request: Promise<T>,
  signal: AbortSignal,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const cancel = () => reject(new TaskPollingCancelled());
    signal.addEventListener('abort', cancel, { once: true });
    request
      .then(resolve, reject)
      .finally(() => signal.removeEventListener('abort', cancel));
    if (signal.aborted) {
      cancel();
    }
  });
}

/** Polls the core task detail API without overlapping requests or exposing task errors. */
export async function pollTask(
  taskId: string,
  options: TaskPollingOptions = {},
): Promise<TaskResult> {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, options.timeoutMs ?? MAX_WAIT_MS);
  options.signal?.addEventListener('abort', cancel, { once: true });
  if (options.signal?.aborted) {
    controller.abort();
  }
  let delay = 0;
  let failures = 0;
  const startedAt = Date.now();
  try {
    while (true) {
      await waitForPoll(delay, controller.signal);
      let response: { result?: TaskResult };
      try {
        response = await withCancellation(
          getJson<{ result?: TaskResult }>(
            `/api/v1/task/${encodeURIComponent(taskId)}`,
          ),
          controller.signal,
        );
      } catch (error) {
        if (controller.signal.aborted) {
          throw new TaskPollingCancelled();
        }
        if (
          error instanceof ApiError &&
          error.status >= 400 &&
          error.status < 500 &&
          error.status !== 408 &&
          error.status !== 429
        ) {
          throw error;
        }
        failures += 1;
        delay = failures === 1 ? 15_000 : 30_000;
        continue;
      }
      if (controller.signal.aborted) {
        throw new TaskPollingCancelled();
      }
      const result = response?.result;
      if (!result || !TASK_STATUSES.includes(result.status)) {
        throw new Error('Invalid task response');
      }
      if (!['pending', 'in_progress', 'aborting'].includes(result.status)) {
        return result;
      }
      failures = 0;
      const elapsed = Date.now() - startedAt;
      delay =
        elapsed >= 5 * 60_000
          ? 30_000
          : elapsed >= 60_000
            ? 15_000
            : POLL_INTERVAL_MS;
    }
  } catch (error) {
    if (timedOut) {
      throw new Error('Task polling timed out');
    }
    throw error;
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener('abort', cancel);
  }
}
