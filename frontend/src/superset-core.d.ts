// These declarations are copied from superset-frontend/packages/superset-core because the
// published `@apache-superset/core` 0.1.0 does not export them yet.
import '@apache-superset/core';

declare module '@apache-superset/core' {
  /** Argument passed to commands contributed to a chart download location. */
  export interface ChartDownloadContext {
    /** Resolves to a query-context object with `result_format: 'json'`. */
    getPayload: () => Promise<Record<string, unknown>>;
    /** Display name of the chart. */
    chartName: string;
    /** Whether the current user may download this chart's data. */
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
