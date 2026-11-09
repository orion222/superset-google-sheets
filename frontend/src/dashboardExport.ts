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

/** Captures the JSON wire representation before authentication or submission yields. */
function snapshotDataMask(
  mask: DashboardDownloadContext['activeDataMask'],
): DashboardDownloadContext['activeDataMask'] {
  return JSON.parse(
    JSON.stringify(mask),
  ) as DashboardDownloadContext['activeDataMask'];
}

/** Queues a dashboard export and follows its task, sharing a watcher for repeated clicks. */
export async function exportDashboard(
  ctx: DashboardDownloadContext,
): Promise<void> {
  if (!ctx.canDownload) {
    toasts.addDangerToast(FAILURE_MESSAGE);
    return;
  }
  let pendingId = toasts.addInfoToast(
    'Preparing dashboard export to Google Sheets…',
    {
      duration: 0,
    },
  );
  const controller = new AbortController();
  const cancel = () => controller.abort();
  window.addEventListener('pagehide', cancel, { once: true });
  let ownsPendingToast = true;
  try {
    const activeDataMask = snapshotDataMask(ctx.activeDataMask);
    const data = await postJson<{ task_id: string; already_running: boolean }>(
      `${EXTENSION_BASE}/dashboard/`,
      { dashboardId: ctx.dashboardId, activeDataMask },
    );
    if (controller.signal.aborted) {
      throw new TaskPollingCancelled();
    }
    if (!data || typeof data.task_id !== 'string' || !UUID.test(data.task_id)) {
      throw new Error('Invalid export response');
    }
    toasts.removeToast(pendingId);
    ownsPendingToast = false;
    const existing = activeExports.get(data.task_id);
    if (existing) {
      await existing;
      return;
    }
    pendingId = toasts.addInfoToast(
      data.already_running
        ? 'Dashboard export already in progress…'
        : 'Dashboard export started…',
      { duration: 0 },
    );
    ownsPendingToast = true;
    const watching = pollTask(data.task_id, { signal: controller.signal })
      .then(task => {
        const url = task.payload?.url;
        if (task.status !== 'success') {
          toasts.addDangerToast(FAILURE_MESSAGE);
          return;
        }
        if (typeof url !== 'string' || !url) {
          throw new Error('Dashboard export did not return a spreadsheet URL');
        }
        toasts.addSuccessToast('Dashboard sent to Google Sheets', {
          duration: 0,
          link: { href: url, label: 'Open in Google Sheets' },
        });
      })
      .catch(error => {
        if (!(error instanceof TaskPollingCancelled)) {
          toasts.addDangerToast(STATUS_FAILURE_MESSAGE);
        }
      })
      .finally(() => {
        activeExports.delete(data.task_id);
        toasts.removeToast(pendingId);
      });
    activeExports.set(data.task_id, watching);
    ownsPendingToast = false;
    await watching;
  } catch (error) {
    if (!(error instanceof TaskPollingCancelled)) {
      toasts.addDangerToast(FAILURE_MESSAGE);
    }
  } finally {
    window.removeEventListener('pagehide', cancel);
    if (ownsPendingToast) {
      toasts.removeToast(pendingId);
    }
  }
}
