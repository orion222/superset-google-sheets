import { authentication } from '@apache-superset/core';

const EXTENSION_BASE = '/extensions/michael-s-molina/sqllab-gsheets';

const APP_ROOT_PATH = /^(?:\/[\w~%-]+(?:\.[\w~%-]+)*)*$/;
let configuredApplicationRoot: string | undefined;

function readApplicationRoot(): string {
  const dataBootstrap = document
    .getElementById('app')
    ?.getAttribute('data-bootstrap');
  if (!dataBootstrap) {
    return '';
  }

  try {
    // The extension API has no URL resolver; use the same page data as the host.
    const bootstrap = JSON.parse(dataBootstrap) as {
      common?: { application_root?: unknown };
    } | null;
    const configuredRoot = bootstrap?.common?.application_root;
    if (typeof configuredRoot !== 'string') {
      return '';
    }
    const root = configuredRoot.replace(/\/$/, '');
    return root && APP_ROOT_PATH.test(root) ? root : '';
  } catch {
    return '';
  }
}

function applicationRoot(): string {
  if (configuredApplicationRoot === undefined) {
    configuredApplicationRoot = readApplicationRoot();
  }
  return configuredApplicationRoot;
}

function extensionUrl(path: string): string {
  return `${applicationRoot()}${EXTENSION_BASE}${path}`;
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
      typeof message === 'string' ? message : '',
      response.status,
    );
  }
  return body as T;
}

export async function postJson<T>(path: `/${string}`, body: unknown): Promise<T> {
  // Serialize before awaiting the token so later caller mutations can't change the request.
  const payload = JSON.stringify(body);
  const csrfToken = await authentication.getCSRFToken();
  const response = await fetch(extensionUrl(path), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrfToken ?? '',
    },
    body: payload,
  });
  return parse<T>(response);
}

async function getUrl<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    method: 'GET',
    ...(signal && { signal }),
  });
  return parse<T>(response);
}

export async function getJson<T>(
  path: `/${string}`,
  signal?: AbortSignal,
): Promise<T> {
  return getUrl<T>(extensionUrl(path), signal);
}

// Superset's own REST API, such as task status, under the configured application path.
export async function getHostJson<T>(
  path: `/api/${string}`,
  signal?: AbortSignal,
): Promise<T> {
  return getUrl<T>(`${applicationRoot()}${path}`, signal);
}
