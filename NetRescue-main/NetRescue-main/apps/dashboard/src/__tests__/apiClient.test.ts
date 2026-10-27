import { afterEach, describe, expect, it, vi } from 'vitest';
import {
    ApiError,
    API_BASE,
    apiRequest,
    describePayload,
    networkMessage,
    resolveApiBase,
} from '../api/client';
import { diagnosticsApi } from '../diagnostics/api';
import { labsApi } from '../labs/api';
import { xferApi } from '../multipath/api';
import { playgroundApi } from '../playground/api';

const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const fail = (p: Promise<unknown>) =>
    p.then(
        () => {
            throw new Error('expected a rejection');
        },
        (e: ApiError) => e,
    );

afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
});

describe('resolveApiBase', () => {
    it('uses the local default only when the page itself is local', () => {
        expect(resolveApiBase(undefined, 'localhost').base).toBe('http://127.0.0.1:8000');
        expect(resolveApiBase('', '127.0.0.1').base).toBe('http://127.0.0.1:8000');
        const hosted = resolveApiBase(undefined, 'netrescue-1.onrender.com');
        expect(hosted.base).toBe('');
        expect(hosted.error).toMatch(/VITE_API_BASE_URL/);
    });

    it('accepts an origin, trimming slashes and whitespace', () => {
        expect(resolveApiBase(' https://netrescue.onrender.com/ ', 'x.com').base).toBe(
            'https://netrescue.onrender.com',
        );
    });

    it('accepts a same-origin relative base', () => {
        expect(resolveApiBase('/proxy/', 'x.com').base).toBe('/proxy');
    });

    it.each(['netrescue.onrender.com', 'ftp://x.com', 'https://x.com/api', 'https://x.com?a=1'])(
        'rejects %s with a clear message',
        (bad) => {
            const r = resolveApiBase(bad, 'x.com');
            expect(r.base).toBe('');
            expect(r.error).toMatch(/VITE_API_BASE_URL/);
        },
    );
});

describe('networkMessage', () => {
    it('never tells a hosted user to start a local server', () => {
        const msg = networkMessage('https://netrescue.onrender.com', 'https://netrescue-1.onrender.com', false);
        expect(msg).not.toMatch(/uvicorn/);
        expect(msg).toMatch(/CORS_ORIGINS must include https:\/\/netrescue-1\.onrender\.com/);
    });

    it('explains a hosted page built against a local API', () => {
        const msg = networkMessage('http://127.0.0.1:8000', 'https://netrescue-1.onrender.com', false);
        expect(msg).toMatch(/VITE_API_BASE_URL/);
        expect(msg).not.toMatch(/uvicorn/);
    });

    it('tells a local developer how to start the API', () => {
        expect(networkMessage('http://127.0.0.1:8000', 'http://localhost:3000', false)).toMatch(/uvicorn/);
    });

    it('reports a timeout distinctly', () => {
        expect(networkMessage('https://a.onrender.com', 'https://b.onrender.com', true)).toMatch(/in time/);
    });
});

describe('describePayload', () => {
    it('reads the shapes the routers return', () => {
        expect(describePayload({ detail: { error: 'ping missing', hint: 'Install it.' } })).toBe('ping missing Install it.');
        expect(describePayload({ detail: { message: 'bad topology' } })).toBe('bad topology');
        expect(
            describePayload({ detail: [{ loc: ['body', 'loss'], msg: 'too big' }] }),
        ).toBe('loss: too big');
    });

    it('explains a 403 as a role limit, not a bug', () => {
        expect(describePayload({ detail: "principal 'x' lacks permission 'write'" }, 403)).toMatch(/operator role/);
    });

    it('falls back to the status code', () => {
        expect(describePayload(null, 502)).toBe('The request failed (HTTP 502).');
    });
});

describe('apiRequest', () => {
    it('sends identity headers and a JSON body to the configured base', async () => {
        const fetchMock = vi.fn().mockResolvedValue(json({ ok: true }));
        vi.stubGlobal('fetch', fetchMock);
        await apiRequest('/api/x', { principal: 'tester', method: 'POST', body: '{"a":1}' });
        const [url, init] = fetchMock.mock.calls[0];
        expect(url).toBe(`${API_BASE}/api/x`);
        expect(init.method).toBe('POST');
        expect(init.headers['X-NetRescue-Principal']).toBe('tester');
        expect(init.headers['X-NetRescue-Role']).toBe('operator');
        expect(init.headers['Content-Type']).toBe('application/json');
        // The browser never carries the shared secret.
        expect(init.headers['X-NetRescue-Token']).toBeUndefined();
    });

    it('classifies a rejected fetch as a network error', async () => {
        vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
        const err = await fail(apiRequest('/x', { principal: 'p' }));
        expect(err).toBeInstanceOf(ApiError);
        expect(err.kind).toBe('network');
        expect(err.status).toBe(0);
    });

    it('times out instead of hanging', async () => {
        vi.stubGlobal(
            'fetch',
            vi.fn((_u: string, init: RequestInit) =>
                new Promise((_res, rej) => {
                    init.signal!.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError')));
                }),
            ),
        );
        const err = await fail(apiRequest('/x', { principal: 'p', timeoutMs: 20 }));
        expect(err.kind).toBe('timeout');
    });

    it('surfaces HTTP errors with the server explanation', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'unknown destination node' }, 422)));
        const err = await fail(apiRequest('/x', { principal: 'p' }));
        expect(err.status).toBe(422);
        expect(err.message).toBe('unknown destination node');
    });

    it('does not echo an HTML error page', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>Bad gateway</html>', { status: 502 })));
        const err = await fail(apiRequest('/x', { principal: 'p' }));
        expect(err.message).toMatch(/HTTP 502/);
        expect(err.message).not.toMatch(/<html>/);
    });

    it('rejects a malformed success body instead of reporting success', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('not json', { status: 200 })));
        const err = await fail(apiRequest('/x', { principal: 'p' }));
        expect(err.kind).toBe('malformed');
    });
});

describe('feature clients use the shared layer', () => {
    const cases: [string, () => Promise<unknown>, string, string, string][] = [
        ['diagnostics', () => diagnosticsApi.status(), 'GET', '/api/diagnostics/status', 'diagnostics-lab'],
        ['diagnostics post', () => diagnosticsApi.faults({ scenario: 'packet_loss' }), 'POST', '/api/diagnostics/faults', 'diagnostics-lab'],
        ['xfer', () => xferApi.state(), 'GET', '/api/xfer/state', 'multipath-lab'],
        ['playground', () => playgroundApi.state(), 'GET', '/api/playground/state', 'playground'],
        ['labs', () => labsApi.routingExample(), 'GET', '/api/labs/routing/example', 'labs'],
    ];

    it.each(cases)('%s', async (_n, call, method, path, principal) => {
        const fetchMock = vi.fn().mockResolvedValue(json({}));
        vi.stubGlobal('fetch', fetchMock);
        await call();
        const [url, init] = fetchMock.mock.calls[0];
        expect(url).toBe(`${API_BASE}${path}`);
        expect(init.method ?? 'GET').toBe(method);
        expect(init.headers['X-NetRescue-Principal']).toBe(principal);
    });

    it('propagates failures from every client', async () => {
        vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'nope' }, 400)));
        for (const call of [
            () => diagnosticsApi.status(),
            () => xferApi.state(),
            () => playgroundApi.state(),
            () => labsApi.routingExample(),
        ]) {
            await expect(call()).rejects.toMatchObject({ status: 400, message: 'nope' });
        }
    });
});
