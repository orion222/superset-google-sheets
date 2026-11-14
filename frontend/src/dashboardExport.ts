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
import { EXTENSION_BASE, postJson } from './client';
import { DashboardDownloadContext } from './coreTypes';
import { pollTask, TaskPollingCancelled } from './taskPolling';

const FAILURE_MESSAGE = 'Dashboard export failed. Please try again.';
const STATUS_FAILURE_MESSAGE =
  'Could not check dashboard export status. View it in the task list.';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const activeExports = new Map<string, Promise<void>>();

interface ExportSubmission {
  task_id: string;
  already_running: boolean;
}

async function watchTask(
  taskId: string,
  alreadyRunning: boolean,
  signal: AbortSignal,
): Promise<void> {
  const pendingId = toasts.addInfoToast(
    alreadyRunning
      ? 'Dashboard export already in progress…'
      : 'Dashboard export started…',
    { duration: 0 },
  );
  try {
    const task = await pollTask(taskId, { signal });
    if (signal.aborted || task.status === 'aborted') {
      return;
    }
    if (task.status !== 'success') {
      // Only this task payload field contains a public message from the worker.
      const message = task.payload?.error_message;
      toasts.addDangerToast(
        task.status === 'failure' && typeof message === 'string' && message.trim()
          ? message
          : FAILURE_MESSAGE,
      );
      return;
    }
    const url = task.payload?.url;
    if (typeof url !== 'string' || !url) {
      throw new Error('Dashboard export did not return a spreadsheet URL');
    }
    toasts.addSuccessToast('Dashboard sent to Google Sheets', {
      duration: 0,
      link: { href: url, label: 'Open in Google Sheets' },
    });
  } catch (error) {
    if (!signal.aborted && !(error instanceof TaskPollingCancelled)) {
      toasts.addDangerToast(STATUS_FAILURE_MESSAGE);
    }
  } finally {
    toasts.removeToast(pendingId);
  }
}

/** Queues a dashboard export and follows its task, sharing a watcher for repeated clicks. */
export async function exportDashboard(
  ctx: DashboardDownloadContext,
): Promise<void> {
  if (!ctx.canDownload) {
    toasts.addDangerToast("You don't have permission to export this dashboard.");
    return;
  }
  const controller = new AbortController();
  const { signal } = controller;
  const cancel = () => controller.abort();
  window.addEventListener('pagehide', cancel, { once: true });
  try {
    const preparingId = toasts.addInfoToast(
      'Preparing dashboard export to Google Sheets…',
      { duration: 0 },
    );
    let data: ExportSubmission;
    try {
      data = await postJson<ExportSubmission>(`${EXTENSION_BASE}/dashboard/`, {
        dashboardId: ctx.dashboardId,
        activeDataMask: ctx.activeDataMask,
      });
      if (!data || typeof data.task_id !== 'string' || !UUID.test(data.task_id)) {
        throw new Error('Invalid export response');
      }
    } catch {
      if (!signal.aborted) {
        toasts.addDangerToast(FAILURE_MESSAGE);
      }
      return;
    } finally {
      toasts.removeToast(preparingId);
    }
    if (signal.aborted) {
      return;
    }
    const existing = activeExports.get(data.task_id);
    if (existing) {
      await existing;
      return;
    }
    const watching = watchTask(data.task_id, data.already_running, signal);
    activeExports.set(data.task_id, watching);
    try {
      await watching;
    } finally {
      activeExports.delete(data.task_id);
    }
  } finally {
    window.removeEventListener('pagehide', cancel);
  }
}
