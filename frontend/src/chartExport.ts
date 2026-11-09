import type { ChartDownloadContext } from '@apache-superset/core';
import { postJson } from './client';
import { runExport } from './exportFlow';

export type ChartResultType = 'full' | 'post_processed';

// `resultType` replaces the payload's own `result_type`, which selects raw versus pivoted data.
export async function exportChart(
  ctx: ChartDownloadContext,
  resultType?: ChartResultType,
): Promise<void> {
  await runExport('Sending to Google Sheets…', async () => {
    const sourcePayload = await ctx.getPayload();
    const payload = resultType
      ? { ...sourcePayload, result_type: resultType }
      : sourcePayload;
    const data = await postJson<{ spreadsheet_url: string }>('/chart/', {
      payload,
      chartName: ctx.chartName,
    });
    return { ...data, message: 'Sent to Google Sheets' };
  });
}
