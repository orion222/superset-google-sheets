// Adds the `toasts` namespace, copied from superset-frontend/packages/superset-core/src/toasts/index.ts;
// the published `@apache-superset/core` 0.1.0 predates it.
import '@apache-superset/core';

declare module '@apache-superset/core' {
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
