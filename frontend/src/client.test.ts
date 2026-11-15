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
import { mockFetch } from '../test/httpMock';

const fetchMock = mockFetch();

async function loadClient(): Promise<{
  client: typeof import('./client');
  authentication: typeof import('@apache-superset/core').authentication;
}> {
  let client!: typeof import('./client');
  let authentication!: typeof import('@apache-superset/core').authentication;
  await jest.isolateModulesAsync(async () => {
    const core = await import('@apache-superset/core');
    client = await import('./client');
    authentication = core.authentication;
  });
  return { client, authentication };
}

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
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ can_export: true }),
  });
});

test.each([
  ['', ''],
  ['/', ''],
  ['/superset', '/superset'],
  ['/superset/', '/superset'],
  ['/team/analytics', '/team/analytics'],
  ['/extensions', '/extensions'],
])('GET and POST respect application_root %j', async (root, prefix) => {
  setBootstrap({ common: { application_root: root } });
  const { client, authentication } = await loadClient();

  await expect(
    client.getJson('/chart/capabilities'),
  ).resolves.toEqual({ can_export: true });
  expect(fetchMock).toHaveBeenLastCalledWith(
    `${prefix}/extensions/michael-s-molina/sqllab-gsheets/chart/capabilities`,
    { method: 'GET' },
  );

  const body = { payload: { queries: [] }, chartName: 'Sales' };
  await client.postJson('/chart/', body);
  expect(authentication.getCSRFToken).toHaveBeenCalledTimes(1);
  expect(fetchMock).toHaveBeenLastCalledWith(
    `${prefix}/extensions/michael-s-molina/sqllab-gsheets/chart/`,
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
  const { client } = await loadClient();
  await client.postJson('/export/', { sql: 'SELECT 1' });
  expect(fetchMock).toHaveBeenLastCalledWith(
    '/superset/extensions/michael-s-molina/sqllab-gsheets/export/',
    expect.objectContaining({ method: 'POST' }),
  );
});

test('a missing bootstrap keeps root deployment URLs', async () => {
  const { client } = await loadClient();
  await client.getJson('/chart/capabilities');
  expect(fetchMock).toHaveBeenLastCalledWith(
    '/extensions/michael-s-molina/sqllab-gsheets/chart/capabilities',
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
  const { client } = await loadClient();
  await client.getJson('/chart/capabilities');
  expect(fetchMock).toHaveBeenLastCalledWith(
    '/extensions/michael-s-molina/sqllab-gsheets/chart/capabilities',
    { method: 'GET' },
  );
});

test('malformed bootstrap JSON keeps root deployment URLs', async () => {
  setBootstrap({});
  document.getElementById('app')?.setAttribute('data-bootstrap', '{');
  const { client } = await loadClient();
  await client.getJson('/chart/capabilities');
  expect(fetchMock).toHaveBeenLastCalledWith(
    '/extensions/michael-s-molina/sqllab-gsheets/chart/capabilities',
    { method: 'GET' },
  );
});
