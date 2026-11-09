import { ApiError, getJson } from './client';

export async function registerIfCapable(
  capabilitiesPath: `/${string}`,
  register: () => void,
): Promise<void> {
  let canExport: boolean;
  try {
    ({ can_export: canExport } = await getJson<{ can_export: boolean }>(
      capabilitiesPath,
    ));
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 403)) {
      console.warn('Extension capability check failed', capabilitiesPath, error);
    }
    return;
  }

  if (canExport) {
    try {
      register();
    } catch (error) {
      console.warn('Extension menu registration failed', capabilitiesPath, error);
    }
  }
}
