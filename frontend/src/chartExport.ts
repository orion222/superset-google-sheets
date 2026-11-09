import { toasts } from '@apache-superset/core';
import { ChartDownloadContext } from './coreTypes';
import { EXTENSION_BASE, postJson } from './client';

export type ChartResultType = 'full' | 'post_processed';

// `resultType` replaces the payload's own `result_type`, which selects raw versus pivoted data.
export async function exportChart(
  ctx: ChartDownloadContext,
  resultType?: ChartResultType,
): Promise<void> {
  const pendingId = toasts.addInfoToast('Sending to Google Sheets…', {
    duration: 0,
  });
  try {
    const payload = await ctx.getPayload();
    if (resultType) {
      payload.result_type = resultType;
    }
    const { url } = await postJson<{ url: string }>(
      `${EXTENSION_BASE}/chart/`,
      {
        payload,
        chartName: ctx.chartName,
        chartId: ctx.chartId,
      },
    );
    toasts.addSuccessToast('Sent to Google Sheets', {
      duration: 0,
      link: { href: url, label: 'Open in Google Sheets' },
    });
  } catch (error) {
    toasts.addDangerToast(
      error instanceof Error && error.message ? error.message : 'Export failed',
    );
  } finally {
    toasts.removeToast(pendingId);
  }
}
