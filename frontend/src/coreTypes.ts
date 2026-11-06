// Copied from superset-frontend/packages/superset-core/src/menus/locations.ts in the Superset repo;
// the published `@apache-superset/core` 0.1.0 does not export them.

/** Request body for `POST /api/v1/chart/data`. */
export interface ChartDataRequest {
  datasource: { id: number; type: string };
  queries: object[];
  result_type: string;
  result_format: string;
  force?: boolean;
  form_data?: object;
}

/** Argument passed to commands contributed to a chart download location. */
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
