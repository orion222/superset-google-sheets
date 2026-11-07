/**
 * @jest-environment node
 *
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

import { execFileSync } from 'node:child_process';
import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { createContext, runInContext } from 'node:vm';
import { DashboardDownloadContext } from '../src/coreTypes';

interface ScriptElement {
  tagName: string;
  src: string;
  parentNode?: { removeChild: (script: ScriptElement) => void };
  onload?: (event: { type: string; target: ScriptElement }) => void;
  onerror?: (event: { type: string; target: ScriptElement }) => void;
  setAttribute: (name: string, value: string) => void;
  getAttribute: (name: string) => string | null;
}

interface FederationContainer {
  init: (scope: Record<string, unknown>) => Promise<void> | void;
  get: (module: string) => Promise<() => unknown>;
}

const frontendDirectory = resolve(__dirname, '..');
const temporaryDirectoryPrefix = 'sqllab-gsheets-bundle-';
const taskId = '07b24a60-50af-46ed-8a33-a8742d5ba601';
const defaultAssetBase =
  'https://superset.test/api/v1/extensions/michael-s-molina/sqllab-gsheets/';
let outputDirectory: string;
let remoteEntry: string;

beforeAll(() => {
  outputDirectory = mkdtempSync(join(tmpdir(), temporaryDirectoryPrefix));
  // Compile the real production configuration, including its publicPath.
  execFileSync(
    process.execPath,
    [
      require.resolve('webpack/bin/webpack.js'),
      '--mode',
      'production',
      '--output-path',
      outputDirectory,
    ],
    { cwd: frontendDirectory, stdio: 'pipe' },
  );
  const entry = readdirSync(outputDirectory).find(file =>
    /^remoteEntry\..+\.js$/.test(file),
  );
  if (!entry) {
    throw new Error('Production build did not emit its federation entry');
  }
  remoteEntry = entry;
}, 60000);

afterAll(() => {
  if (outputDirectory) {
    const resolvedOutputDirectory = resolve(outputDirectory);
    if (
      dirname(resolvedOutputDirectory) !== resolve(tmpdir()) ||
      !basename(resolvedOutputDirectory).startsWith(temporaryDirectoryPrefix)
    ) {
      throw new Error(
        'Refusing to remove a directory outside the temporary build location',
      );
    }
    rmSync(resolvedOutputDirectory, { recursive: true, force: true });
  }
});

/** Loads the emitted federation bundle with browser lifecycle and API stand-ins. */
async function loadProductionExtension(
  prefix = '',
  assetBase = defaultAssetBase,
  taskStatus = 200,
) {
  const origin = 'https://superset.test';
  const requestedChunks: string[] = [];
  const scripts: ScriptElement[] = [];
  let chunkEvaluationError: unknown;
  const commands = { registerCommand: jest.fn() };
  const menus = { registerMenuItem: jest.fn() };
  const toasts = {
    addInfoToast: jest.fn(() => 'pending-id'),
    addSuccessToast: jest.fn(),
    addDangerToast: jest.fn(),
    removeToast: jest.fn(),
  };
  const windowEvents = new EventTarget();
  const documentEvents = new EventTarget();
  const fetchMock = jest.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === 'POST') {
      return {
        ok: true,
        status: 202,
        json: async () => ({ task_id: taskId, already_running: false }),
      };
    }
    if (url === `${prefix}/api/v1/task/${taskId}`) {
      return {
        ok: taskStatus === 200,
        status: taskStatus,
        json: async () =>
          taskStatus === 200
            ? { result: { status: 'pending' } }
            : { message: 'Forbidden' },
      };
    }
    return {
      ok: ['chart', 'dashboard'].some(
        surface =>
          url ===
          `${prefix}/extensions/michael-s-molina/sqllab-gsheets/${surface}/capabilities`,
      ),
      status: 200,
      json: async () => ({ can_export: true }),
    };
  });

  function createScript(): ScriptElement {
    const attributes = new Map<string, string>();
    return {
      tagName: 'SCRIPT',
      src: '',
      setAttribute(name, value) {
        attributes.set(name, value);
      },
      getAttribute(name) {
        return name === 'src' ? this.src : (attributes.get(name) ?? null);
      },
    };
  }

  const entryScript = createScript();
  entryScript.src = `${assetBase}${remoteEntry}`;
  scripts.push(entryScript);
  const document = {
    currentScript: entryScript,
    hidden: false,
    addEventListener: documentEvents.addEventListener.bind(documentEvents),
    removeEventListener:
      documentEvents.removeEventListener.bind(documentEvents),
    getElementById: (id: string) =>
      id === 'app'
        ? {
            getAttribute: (name: string) =>
              name === 'data-bootstrap'
                ? JSON.stringify({
                    common: { application_root: prefix || '/' },
                  })
                : null,
          }
        : null,
    getElementsByTagName: (tag: string) => (tag === 'script' ? scripts : []),
    createElement: createScript,
    head: {
      removeChild: (script: ScriptElement) => {
        const index = scripts.indexOf(script);
        if (index >= 0) scripts.splice(index, 1);
      },
      appendChild: (script: ScriptElement) => {
        scripts.push(script);
        script.parentNode = document.head;
        const url = new URL(script.src, origin).href;
        requestedChunks.push(url);
        queueMicrotask(() => {
          // A prefixed deployment serves extension assets only below its root.
          if (!url.startsWith(assetBase)) {
            script.onerror?.({ type: 'error', target: script });
            return;
          }
          const previousScript = document.currentScript;
          document.currentScript = script;
          try {
            runInContext(
              readFileSync(
                join(outputDirectory, basename(new URL(url).pathname)),
                'utf8',
              ),
              context,
              { filename: url },
            );
            script.onload?.({ type: 'load', target: script });
          } catch (error) {
            chunkEvaluationError = error;
            script.onerror?.({ type: 'error', target: script });
          } finally {
            document.currentScript = previousScript;
          }
        });
        return script;
      },
    },
  };
  const browser = {
    document,
    location: new URL(`${origin}${prefix}/superset/explore/`),
    superset: {
      commands,
      menus,
      authentication: { getCSRFToken: async () => 'test-csrf' },
      sqlLab: { getCurrentTab: () => undefined },
      toasts,
    },
    addEventListener: windowEvents.addEventListener.bind(windowEvents),
    removeEventListener: windowEvents.removeEventListener.bind(windowEvents),
    fetch: fetchMock,
    URL,
    AbortController,
    setTimeout,
    clearTimeout,
    console,
  };
  const context = createContext({
    ...browser,
    window: browser,
    self: browser,
  });
  runInContext(
    readFileSync(join(outputDirectory, remoteEntry), 'utf8'),
    context,
    {
      filename: entryScript.src,
    },
  );
  const container = context.michaelSMolina_sqllabGsheets as FederationContainer;
  await container.init({});
  const evaluateModule = await container.get('./index').catch(error => {
    throw chunkEvaluationError ?? error;
  });
  evaluateModule();
  await new Promise<void>(resolvePromise => setImmediate(resolvePromise));

  const dashboardCommand = commands.registerCommand.mock.calls.find(
    ([command]) => command.id === 'sqllab_gsheets.exportDashboard',
  )?.[1] as ((ctx: DashboardDownloadContext) => Promise<void>) | undefined;
  return {
    requestedChunks,
    commands,
    menus,
    fetchMock,
    toasts,
    leavePage: () => windowEvents.dispatchEvent(new Event('pagehide')),
    exportDashboard: () => {
      if (!dashboardCommand) {
        throw new Error('Dashboard export command was not registered');
      }
      return dashboardCommand({
        dashboardId: 42,
        dashboardTitle: 'Sales',
        activeDataMask: {},
        canDownload: true,
      });
    },
  };
}

test.each([
  {
    prefix: '',
    assetBase: defaultAssetBase,
  },
  {
    prefix: '/superset',
    assetBase:
      'https://superset.test/superset/api/v1/extensions/michael-s-molina/sqllab-gsheets/',
  },
  {
    prefix: '/superset',
    assetBase:
      'https://assets.example/extensions/michael-s-molina/sqllab-gsheets/',
  },
])(
  'loads production chunks and menus at "$prefix" with assets at $assetBase',
  async ({ prefix, assetBase }) => {
    const { requestedChunks, fetchMock, commands, menus } =
      await loadProductionExtension(prefix, assetBase);

    expect(requestedChunks.length).toBeGreaterThan(0);
    expect(requestedChunks.every(url => url.startsWith(assetBase))).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      `${prefix}/extensions/michael-s-molina/sqllab-gsheets/chart/capabilities`,
      { method: 'GET' },
    );
    expect(fetchMock).toHaveBeenCalledWith(
      `${prefix}/extensions/michael-s-molina/sqllab-gsheets/dashboard/capabilities`,
      { method: 'GET' },
    );
    expect(commands.registerCommand).toHaveBeenCalledTimes(4);
    expect(menus.registerMenuItem).toHaveBeenCalledTimes(9);
  },
);

test('production dashboard polling cancels on pagehide without an error toast', async () => {
  jest.useFakeTimers({ doNotFake: ['queueMicrotask', 'setImmediate'] });
  const extension = await loadProductionExtension();
  try {
    const exporting = extension.exportDashboard();
    await jest.advanceTimersByTimeAsync(0);
    expect(extension.fetchMock).toHaveBeenCalledWith(`/api/v1/task/${taskId}`, {
      method: 'GET',
    });
    extension.leavePage();
    await exporting;
    expect(extension.toasts.addDangerToast).not.toHaveBeenCalled();
    expect(extension.toasts.addSuccessToast).not.toHaveBeenCalled();
    expect(jest.getTimerCount()).toBe(0);
  } finally {
    extension.leavePage();
    jest.clearAllTimers();
    jest.useRealTimers();
  }
});

test('production dashboard polling stops immediately on a forbidden task response', async () => {
  jest.useFakeTimers({ doNotFake: ['queueMicrotask', 'setImmediate'] });
  const extension = await loadProductionExtension('', defaultAssetBase, 403);
  try {
    const exporting = extension.exportDashboard();
    await jest.advanceTimersByTimeAsync(0);
    expect(extension.toasts.addDangerToast).toHaveBeenCalledWith(
      'Could not check dashboard export status. View it in the task list.',
    );
    expect(jest.getTimerCount()).toBe(0);
    await jest.advanceTimersByTimeAsync(30_000);
    expect(
      extension.fetchMock.mock.calls.filter(([url]) =>
        url.startsWith('/api/v1/task/'),
      ),
    ).toHaveLength(1);
    await exporting;
  } finally {
    extension.leavePage();
    jest.clearAllTimers();
    jest.useRealTimers();
  }
});
