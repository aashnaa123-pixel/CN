/**
 * client.ts — the single HTTP layer every feature talks to the backend through.
 *
 * One place decides the API base URL, builds the identity headers, applies a
 * timeout, and turns every failure into an ApiError whose message says what
 * actually went wrong. Nothing here holds a secret: the browser only names the
 * role it wants (X-NetRescue-Role); whether the server grants it is the server's
 * decision (see PUBLIC_DEMO in docs/DEPLOYMENT.md). A token is never read from
 * VITE_* variables because those are baked into public JavaScript.
 */

export type ErrorKind = 'config' | 'network' | 'timeout' | 'http' | 'malformed';

export class ApiError extends Error {
    constructor(
        message: string,
        readonly status: number,
        readonly detail?: unknown,
        readonly kind: ErrorKind = 'http',
    ) {
        super(message);
        this.name = 'ApiError';
    }
}

export const LOCAL_DEFAULT_BASE = 'http://127.0.0.1:8000';
export const DEFAULT_TIMEOUT_MS = 60_000;

const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);

export interface ResolvedBase {
    /** Base URL without a trailing slash; '' when unusable. */
    base: string;
    /** Why the base is unusable, when it is. */
    error?: string;
}

/** Pure so it can be tested: the raw env value and the page's own hostname. */
export function resolveApiBase(raw: string | undefined, pageHostname: string): ResolvedBase {
    const value = (raw ?? '').trim();
    if (!value) {
        // A local default is only sensible when the page itself is local.
        if (LOOPBACK_HOSTS.has(pageHostname) || pageHostname === '') {
            return { base: LOCAL_DEFAULT_BASE };
        }
        return {
            base: '',
            error:
                'This site was built without an API address. Set VITE_API_BASE_URL to the ' +
                'backend URL (for example https://your-api.onrender.com) and rebuild the frontend.',
        };
    }
    if (value.startsWith('/')) return { base: value.replace(/\/+$/, '') };
    let url: URL;
    try {
        url = new URL(value);
    } catch {
        return { base: '', error: `VITE_API_BASE_URL is not a valid URL: "${value}".` };
    }
    if (url.protocol !== 'http:' && url.protocol !== 'https:') {
        return { base: '', error: `VITE_API_BASE_URL must start with http:// or https://, got "${value}".` };
    }
    if (url.pathname.replace(/\/+$/, '') !== '' || url.search || url.hash) {
        return {
            base: '',
            error: `VITE_API_BASE_URL must be an origin with no path, got "${value}". Use e.g. ${url.origin}.`,
        };
    }
    return { base: url.origin };
}

const pageHost = typeof window !== 'undefined' ? window.location.hostname : '';
const resolved = resolveApiBase(import.meta.env.VITE_API_BASE_URL, pageHost);

export const API_BASE = resolved.base;
export const API_CONFIG_ERROR = resolved.error;

/** True when the backend is not on this machine (the deployed-site case). */
export const IS_REMOTE_API = (() => {
    if (!API_BASE || API_BASE.startsWith('/')) return false;
    try {
        return !LOOPBACK_HOSTS.has(new URL(API_BASE).hostname);
    } catch {
        return false;
    }
})();

/** What went wrong when fetch() itself rejected — worded for where the app is running. */
export function networkMessage(base: string, pageOrigin: string, timedOut: boolean): string {
    if (timedOut) {
        return (
            `The NetRescue API at ${base} did not answer in time. If it is hosted on a free ` +
            'plan it may be waking from sleep; wait a minute and try again.'
        );
    }
    const apiOrigin = base.startsWith('/') ? pageOrigin : new URL(base).origin;
    const apiLocal = LOOPBACK_HOSTS.has(new URL(apiOrigin).hostname);
    const pageLocal = LOOPBACK_HOSTS.has(new URL(pageOrigin).hostname);

    if (apiLocal && pageLocal) {
        return (
            `Could not reach the NetRescue API at ${base}. Start it locally with: ` +
            'uvicorn apps.api.main:app --host 127.0.0.1 --port 8000'
        );
    }
    if (apiLocal && !pageLocal) {
        return (
            `This site (${pageOrigin}) is configured to call ${base}, which is a local ` +
            'address and cannot be reached from a hosted page. The frontend needs ' +
            'VITE_API_BASE_URL set to the hosted backend URL.'
        );
    }
    if (apiOrigin !== pageOrigin) {
        return (
            `Could not reach the NetRescue API at ${base} from ${pageOrigin}. The server may be ` +
            `offline or still waking up, or it may be rejecting this site's origin: the backend's ` +
            `CORS_ORIGINS must include ${pageOrigin}. (Browsers report both cases identically.)`
        );
    }
    return `Could not reach the NetRescue API at ${base}. Check that the server is running.`;
}

/** Extract the most specific human-readable reason from an error response body. */
export function describePayload(payload: unknown, status = 0): string {
    const detail =
        payload && typeof payload === 'object' && 'detail' in payload
            ? (payload as { detail: unknown }).detail
            : payload;

    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
        const d = detail as Record<string, unknown>;
        // diagnostics: { error, hint }; playground/xfer: { message }
        if (typeof d.error === 'string') return d.hint ? `${d.error} ${String(d.hint)}` : d.error;
        if ('message' in d) return String(d.message);
    }
    if (typeof detail === 'string' && detail) {
        if (status === 403) {
            return (
                `${detail} This API grants public visitors the operator role only for ` +
                'simulation features; real-host operations need the API token or a local install.'
            );
        }
        return detail;
    }
    if (Array.isArray(detail) && detail.length) {
        return detail
            .map((d) => {
                if (typeof d === 'object' && d && 'msg' in d) {
                    const loc = ((d as { loc?: unknown[] }).loc ?? []).filter((x) => x !== 'body');
                    const msg = String((d as { msg: unknown }).msg);
                    return loc.length ? `${loc.join('.')}: ${msg}` : msg;
                }
                return String(d);
            })
            .join('; ');
    }
    if (status) return `The request failed (HTTP ${status}).`;
    return 'The request failed.';
}

export interface RequestOptions extends Omit<RequestInit, 'headers'> {
    /** Identity this feature reports in X-NetRescue-Principal (audit trail). */
    principal: string;
    headers?: Record<string, string>;
    timeoutMs?: number;
}

function identityHeaders(principal: string): Record<string, string> {
    return {
        'X-NetRescue-Principal': import.meta.env.VITE_PRINCIPAL ?? principal,
        'X-NetRescue-Role': import.meta.env.VITE_ROLE ?? 'operator',
    };
}

/** fetch() with base URL, identity headers, timeout and classified failures. */
export async function apiFetch(path: string, opts: RequestOptions): Promise<Response> {
    if (API_CONFIG_ERROR) throw new ApiError(API_CONFIG_ERROR, 0, undefined, 'config');

    const { principal, headers, timeoutMs = DEFAULT_TIMEOUT_MS, ...init } = opts;
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
        timedOut = true;
        controller.abort();
    }, timeoutMs);
    try {
        return await fetch(`${API_BASE}${path}`, {
            ...init,
            signal: controller.signal,
            headers: {
                ...identityHeaders(principal),
                ...(init.body ? { 'Content-Type': 'application/json' } : {}),
                ...headers,
            },
        });
    } catch (cause) {
        throw new ApiError(
            networkMessage(API_BASE, window.location.origin, timedOut),
            0,
            cause,
            timedOut ? 'timeout' : 'network',
        );
    } finally {
        clearTimeout(timer);
    }
}

/** JSON request: returns the parsed body or throws an ApiError. */
export async function apiRequest<T>(path: string, opts: RequestOptions): Promise<T> {
    const response = await apiFetch(path, opts);
    if (response.status === 204) return undefined as T;

    let text: string;
    try {
        text = await response.text();
    } catch (cause) {
        throw new ApiError(
            'The connection dropped while reading the API response.',
            response.status,
            cause,
            'network',
        );
    }

    let payload: unknown = null;
    let parsed = true;
    if (text) {
        try {
            payload = JSON.parse(text);
        } catch {
            parsed = false;
            payload = text;
        }
    }

    if (!response.ok) {
        // A proxy/error page (HTML) is not a NetRescue explanation; do not echo markup.
        const message =
            parsed || !text.trimStart().startsWith('<')
                ? describePayload(payload, response.status)
                : `The server returned HTTP ${response.status} with a non-JSON body. It may be restarting or misconfigured.`;
        throw new ApiError(message, response.status, payload, 'http');
    }
    if (!parsed) {
        throw new ApiError(
            `The API returned a malformed (non-JSON) response for ${path}.`,
            response.status,
            payload,
            'malformed',
        );
    }
    return payload as T;
}
