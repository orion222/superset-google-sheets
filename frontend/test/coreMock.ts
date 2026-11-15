// Jest stand-in for `@apache-superset/core`, which webpack externalises to `window.superset` at runtime.
export const commands = { registerCommand: jest.fn() };
export const menus = { registerMenuItem: jest.fn() };
export const authentication = {
  getCSRFToken: jest.fn(async () => 'csrf-token'),
};
export const sqlLab = { getCurrentTab: jest.fn() };
export const toasts = {
  addInfoToast: jest.fn(() => 'pending-id'),
  addSuccessToast: jest.fn(() => 'success-id'),
  addWarningToast: jest.fn(() => 'warning-id'),
  addDangerToast: jest.fn(() => 'danger-id'),
  removeToast: jest.fn(),
};
