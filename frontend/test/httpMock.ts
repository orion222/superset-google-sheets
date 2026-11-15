type FetchResponse = Pick<Response, 'ok' | 'status' | 'json'>;

export function mockFetch(): jest.Mock<
  Promise<FetchResponse>,
  Parameters<typeof fetch>
> {
  const originalFetch = global.fetch;
  const fetchMock = jest.fn<Promise<FetchResponse>, Parameters<typeof fetch>>();
  global.fetch = fetchMock as unknown as typeof fetch;
  afterAll(() => {
    global.fetch = originalFetch;
  });
  return fetchMock;
}

export const ok = (body: unknown): FetchResponse => ({
  ok: true,
  status: 200,
  json: async () => body,
});

export const err = (status: number, body: unknown): FetchResponse => ({
  ok: false,
  status,
  json: async () => body,
});
