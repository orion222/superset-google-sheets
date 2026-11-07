import { authentication } from '@apache-superset/core';

export const EXTENSION_BASE = '/extensions/michael-s-molina/sqllab-gsheets';

const DEFAULT_MESSAGE = 'Export failed';
const APP_ROOT_PATH = /^(?:\/[\w~%-]+(?:\.[\w~%-]+)*)*$/;

function withApplicationRoot(path: string): string {
  const dataBootstrap = document
    .getElementById('app')
    ?.getAttribute('data-bootstrap');
  if (!dataBootstrap) {
    return path;
  }

  try {
    // The extension API has no URL resolver; use the same page data as the host.
    const bootstrap = JSON.parse(dataBootstrap) as {
      common?: { application_root?: unknown };
    } | null;
    const configuredRoot = bootstrap?.common?.application_root;
    if (typeof configuredRoot !== 'string') {
      return path;
    }
    const root = configuredRoot.replace(/\/$/, '');
    if (
      !root ||
      !APP_ROOT_PATH.test(root) ||
      !path.startsWith('/') ||
      path.startsWith('//') ||
      path === root ||
      path.startsWith(`${root}/`)
    ) {
      return path;
    }
    return `${root}${path}`;
  } catch {
    return path;
  }
}

// Carries the HTTP status so callers can tell a permission failure from a bad request.
export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    Object.setPrototypeOf(this, new.target.prototype);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function parse<T>(response: Response): Promise<T> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  if (!response.ok) {
    const message = (body as { message?: unknown } | undefined)?.message;
    throw new ApiError(
      typeof message === 'string' && message ? message : DEFAULT_MESSAGE,
      response.status,
    );
  }
  return body as T;
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const csrfToken = await authentication.getCSRFToken();
  const response = await fetch(withApplicationRoot(path), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrfToken ?? '',
    },
    body: JSON.stringify(body),
  });
  return parse<T>(response);
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(withApplicationRoot(path), { method: 'GET' });
  return parse<T>(response);
}
