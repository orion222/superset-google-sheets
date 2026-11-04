import { authentication } from '@apache-superset/core';

export const EXTENSION_BASE = '/extensions/michael-s-molina/sqllab-gsheets';

const DEFAULT_MESSAGE = 'Export failed';

// Carries the HTTP status so callers can tell a permission failure from a bad request.
export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
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
  const response = await fetch(path, {
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
  const response = await fetch(path, { method: 'GET' });
  return parse<T>(response);
}
