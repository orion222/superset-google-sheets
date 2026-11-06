// Copied from superset-frontend/packages/superset-core/src/menus/locations.ts in the Superset repo;
// the published `@apache-superset/core` 0.1.0 does not export them.

/** Argument passed to commands contributed to a chart download location. */
export interface ChartDownloadContext {
  /** Resolves to a query-context object with `result_format: 'json'`. */
  getPayload: () => Promise<Record<string, unknown>>;
  /** Display name of the chart. */
  chartName: string;
  /** ID of the saved chart the data comes from; absent for an unsaved chart. */
  chartId?: number;
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
