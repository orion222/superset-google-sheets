/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
import { authentication } from '@apache-superset/core';
import { EXTENSION_BASE, getJson, postJson } from './client';

const fetchMock = jest.fn<
  Promise<Pick<Response, 'ok' | 'status' | 'json'>>,
  Parameters<typeof fetch>
>();
const originalFetch = global.fetch;

function setBootstrap(value: unknown): void {
  const app = document.createElement('div');
  app.id = 'app';
  app.setAttribute('data-bootstrap', JSON.stringify(value));
  document.body.replaceChildren(app);
}

beforeEach(() => {
  jest.clearAllMocks();
  document.body.replaceChildren();
  // The active route is unrelated to the operator's configured application path.
  window.history.replaceState(null, '', '/unrelated/explore/');
  global.fetch = fetchMock as unknown as typeof fetch;
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ can_export: true }),
  });
});

afterAll(() => {
  global.fetch = originalFetch;
});

test.each([
  ['', ''],
  ['/', ''],
  ['/superset', '/superset'],
  ['/superset/', '/superset'],
  ['/team/analytics', '/team/analytics'],
])('GET and POST respect application_root %j', async (root, prefix) => {
  setBootstrap({ common: { application_root: root } });

  await expect(
    getJson(`${EXTENSION_BASE}/chart/capabilities`),
  ).resolves.toEqual({ can_export: true });
  expect(fetchMock).toHaveBeenLastCalledWith(
    `${prefix}${EXTENSION_BASE}/chart/capabilities`,
    { method: 'GET' },
  );

  const body = { payload: { queries: [] }, chartName: 'Sales' };
  await postJson(`${EXTENSION_BASE}/chart/`, body);
  expect(authentication.getCSRFToken).toHaveBeenCalledTimes(1);
  expect(fetchMock).toHaveBeenLastCalledWith(
    `${prefix}${EXTENSION_BASE}/chart/`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': 'csrf-token',
      },
      body: JSON.stringify(body),
    },
  );
});

test('SQL Lab exports use the configured application path', async () => {
  setBootstrap({ common: { application_root: '/superset' } });
  await postJson(`${EXTENSION_BASE}/export/`, { sql: 'SELECT 1' });
  expect(fetchMock).toHaveBeenLastCalledWith(
    `/superset${EXTENSION_BASE}/export/`,
    expect.objectContaining({ method: 'POST' }),
  );
});

test('an already prefixed URL is not prefixed twice', async () => {
  setBootstrap({ common: { application_root: '/superset' } });
  await getJson(`/superset${EXTENSION_BASE}/chart/capabilities`);
  expect(fetchMock).toHaveBeenLastCalledWith(
    `/superset${EXTENSION_BASE}/chart/capabilities`,
    { method: 'GET' },
  );
});

test('a missing bootstrap keeps root deployment URLs', async () => {
  await getJson(`${EXTENSION_BASE}/chart/capabilities`);
  expect(fetchMock).toHaveBeenLastCalledWith(
    `${EXTENSION_BASE}/chart/capabilities`,
    { method: 'GET' },
  );
});

test.each([
  null,
  {},
  { common: null },
  { common: { application_root: 42 } },
  { common: { application_root: 'https://other.example' } },
  { common: { application_root: '//other.example' } },
  { common: { application_root: '/superset/..' } },
])('invalid or absent application_root falls back to root: %j', async value => {
  setBootstrap(value);
  await getJson(`${EXTENSION_BASE}/chart/capabilities`);
  expect(fetchMock).toHaveBeenLastCalledWith(
    `${EXTENSION_BASE}/chart/capabilities`,
    { method: 'GET' },
  );
});

test('malformed bootstrap JSON keeps root deployment URLs', async () => {
  setBootstrap({});
  document.getElementById('app')?.setAttribute('data-bootstrap', '{');
  await getJson(`${EXTENSION_BASE}/chart/capabilities`);
  expect(fetchMock).toHaveBeenLastCalledWith(
    `${EXTENSION_BASE}/chart/capabilities`,
    { method: 'GET' },
  );
});
