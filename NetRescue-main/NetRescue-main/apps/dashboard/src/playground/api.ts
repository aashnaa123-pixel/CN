/**
 * api.ts — the only place the playground talks to the backend.
 *
 * Two rules, the same two the operator console follows:
 *
 *  1. Nothing resolves until the backend has confirmed it. There is no
 *     optimistic "link disabled" in the UI — the panel waits for the runtime's
 *     answer, because a failure that only exists in a React state object is not
 *     a failure.
 *  2. Errors carry the server's own explanation. When the API refuses a
 *     topology, the reason it gave is what the operator reads.
 */
import type {
    DeliveryResult,
    DetectorComparison,
    ExperimentEvent,
    PlaygroundState,
    TopologyPayload,
    ValidationReport,
} from './types';

import { apiRequest, ApiError, API_BASE, describePayload } from '../api/client';

export { API_BASE, ApiError };

// All HTTP concerns (base URL, identity headers, timeout, error wording) live in
// ../api/client so every feature behaves the same way.
const request = <T>(path: string, init: RequestInit = {}): Promise<T> =>
    apiRequest<T>(path, {
        principal: 'playground',
        timeoutMs: 120_000,
        ...init,
        headers: init.headers as Record<string, string> | undefined,
    });

export const playgroundApi = {
    state: () => request<PlaygroundState>('/api/playground/state'),

    save: (payload: TopologyPayload) =>
        request<PlaygroundState>('/api/playground/topology', {
            method: 'PUT',
            body: JSON.stringify(payload),
        }),

    example: () =>
        request<PlaygroundState>('/api/playground/topology/example', { method: 'POST' }),

    clear: () =>
        request<PlaygroundState>('/api/playground/topology/clear', { method: 'POST' }),

    validate: (source?: string | null, destination?: string | null) =>
        request<PlaygroundState>('/api/playground/validate', {
            method: 'POST',
            body: JSON.stringify({ source, destination }),
        }),

    apply: (options: {
        heartbeat_interval_s: number;
        f_thresh: number;
        n: number;
        r_thresh: number;
    }) =>
        request<PlaygroundState>('/api/playground/apply', {
            method: 'POST',
            body: JSON.stringify(options),
        }),

    teardown: () =>
        request<PlaygroundState>('/api/playground/teardown', { method: 'POST' }),

    test: (source: string, destination: string) =>
        request<DeliveryResult>('/api/playground/test', {
            method: 'POST',
            body: JSON.stringify({ source, destination }),
        }),

    failNode: (id: string) =>
        request<PlaygroundState>(`/api/playground/nodes/${encodeURIComponent(id)}/fail`, {
            method: 'POST',
        }),

    restoreNode: (id: string) =>
        request<PlaygroundState>(`/api/playground/nodes/${encodeURIComponent(id)}/restore`, {
            method: 'POST',
        }),

    failLink: (id: string) =>
        request<PlaygroundState>(`/api/playground/links/${encodeURIComponent(id)}/fail`, {
            method: 'POST',
        }),

    restoreLink: (id: string) =>
        request<PlaygroundState>(`/api/playground/links/${encodeURIComponent(id)}/restore`, {
            method: 'POST',
        }),

    detectors: (linkId?: string) =>
        request<{
            detectors: Record<string, Record<string, import('./types').DetectorReading>>;
            transitions: import('./types').DetectorTransition[];
            states_implemented: string[];
            note: string;
        }>(`/api/playground/detectors${linkId ? `?link_id=${encodeURIComponent(linkId)}` : ''}`),

    configureDetector: (config: {
        link_id: string;
        f_thresh?: number;
        n?: number;
        r_thresh?: number;
        use_ewma?: boolean;
    }) =>
        request<{
            detectors: import('./types').DetectorReading[];
            hysteresis_ratio: number | null;
            snapshot: PlaygroundState;
        }>('/api/playground/detectors', {
            method: 'POST',
            body: JSON.stringify(config),
        }),

    transitions: (linkId?: string) =>
        request<{
            transitions: import('./types').DetectorTransition[];
            count: number;
            states_implemented: string[];
        }>(`/api/playground/transitions${linkId ? `?link_id=${encodeURIComponent(linkId)}` : ''}`),

    compare: (body: {
        link_id: string;
        sequence: boolean[];
        f_thresh: number;
        n: number;
        r_thresh: number;
        use_ewma: boolean;
        baseline_threshold?: number;
    }) =>
        request<DetectorComparison>('/api/playground/hysteresis/compare', {
            method: 'POST',
            body: JSON.stringify(body),
        }),

    events: () => request<{ events: ExperimentEvent[]; count: number }>('/api/playground/events'),

    clearEvents: () =>
        request<PlaygroundState>('/api/playground/events/clear', { method: 'POST' }),

    export: (fmt: 'json' | 'csv') =>
        request<{ filename: string; content: string; rows?: number }>(
            `/api/playground/export?fmt=${fmt}`,
        ),
};

/** Hand a generated file to the browser as a real download. */
export function download(filename: string, content: string, mime = 'text/plain') {
    const blob = new Blob([content], { type: `${mime};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
}

export type { ValidationReport };