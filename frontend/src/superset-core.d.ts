// These declarations are copied from superset-frontend/packages/superset-core because the
// published `@apache-superset/core` 0.1.0 does not export them yet.
import '@apache-superset/core';

declare module '@apache-superset/core' {
  /** Argument passed to commands contributed to a chart download location. */
  /** Request body for `POST /api/v1/chart/data`. */
  export interface ChartDataRequest {
    datasource: { id: number; type: string };
    queries: object[];
    result_type: string;
    result_format: string;
    force?: boolean;
    form_data?: object;
  }

  export interface ChartDownloadContext {
    /**
     * Resolves to the chart-data request the CSV export would send, with
     * `result_format: 'json'`. Evaluated when called, so invoke it from the
     * command rather than ahead of time.
     */
    getPayload: () => Promise<ChartDataRequest>;
    /** ID of the saved chart the data comes from; absent for an unsaved chart. */
    chartId?: number;
    /** Display name of the chart. */
    chartName: string;
    /** Whether the current user may download this chart's data. */
    canDownload: boolean;
  }

  /** Argument passed to commands contributed to the dashboard download location. */
  export interface DashboardDownloadContext {
    /** Numeric id of the dashboard. */
    dashboardId: number;
    /** Title of the dashboard. */
    dashboardTitle: string;
    /** Currently applied native filters, keyed by filter id. */
    activeDataMask: Record<
      string,
      {
        extraFormData: object;
        filterState: { value?: unknown; label?: string };
      }
    >;
    /** Whether the current user may download this dashboard's data. */
    canDownload: boolean;
  }

  export namespace toasts {
    interface ToastOptions {
      link?: { href: string; label: string };
      /** Milliseconds before the toast closes; `0` keeps it until closed. */
      duration?: number;
    }
    function addInfoToast(text: string, options?: ToastOptions): string;
    function addSuccessToast(text: string, options?: ToastOptions): string;
    function addWarningToast(text: string, options?: ToastOptions): string;
    function addDangerToast(text: string, options?: ToastOptions): string;
    function removeToast(id: string): void;
  }
}
