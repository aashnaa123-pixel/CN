/**
 * api.ts — the only place the diagnostics lab talks to the backend.
 *
 * The same two rules the other labs follow: a panel is only populated from a
 * response the backend actually returned, and an error carries the server's own
 * explanation rather than a generic message. Nothing here invents a latency, a
 * packet-loss figure or a "healthy" verdict.
 */
import type {
    BaselineComparison,
    DiagnosticsRun,
    DiagnosticsStatus,
    DnsResult,
    FaultScenarioResult,
    FeatureBPair,
    HysteresisSweep,
    IpConfigurationResult,
    PingResult,
    RunComparison,
    RunHistory,
    SchedulerComparison,
    ScenariosResponse,
    TracerouteResult,
} from './types';

import { apiFetch, apiRequest, ApiError, API_BASE, describePayload } from '../api/client';

export { API_BASE, ApiError };

// All HTTP concerns (base URL, identity headers, timeout, error wording) live in
// ../api/client so every feature behaves the same way.
const request = <T>(path: string, init: RequestInit = {}): Promise<T> =>
    apiRequest<T>(path, {
        principal: 'diagnostics-lab',
        timeoutMs: 300_000,
        ...init,
        headers: init.headers as Record<string, string> | undefined,
    });

export interface RunBody {
    target?: string;
    count?: number;
    timeout_s?: number;
    include_traceroute?: boolean;
    max_hops?: number;
    trace_timeout_s?: number;
    include_adapters?: boolean;
    dns_name?: string;
    dns_timeout_s?: number;
}

export interface BenchmarkBody {
    target: string;
    count: number;
    timeout_s: number;
    repetitions: number;
    interval_s?: number;
    label?: string;
}

export interface FaultBody {
    scenario: string;
    seed?: number;
    count?: number;
    repetitions?: number;
    timeout_s?: number;
    latency_ms?: number;
    jitter_ms?: number;
    loss_rate?: number;
    period?: number;
    target?: string;
}

export const diagnosticsApi = {
    status: () => request<DiagnosticsStatus>('/api/diagnostics/status'),

    scenarios: () => request<ScenariosResponse>('/api/diagnostics/scenarios'),

    network: () =>
        request<{ adapters: import('./types').AdaptersResult; ip_configuration: IpConfigurationResult }>(
            '/api/diagnostics/network',
        ),

    ping: (body: { target: string; count?: number; timeout_s?: number; size_bytes?: number }) =>
        request<PingResult>('/api/diagnostics/ping', { method: 'POST', body: JSON.stringify(body) }),

    traceroute: (body: { target: string; max_hops?: number; timeout_s?: number }) =>
        request<TracerouteResult>('/api/diagnostics/traceroute', {
            method: 'POST',
            body: JSON.stringify(body),
        }),

    dns: (body: { name: string; server?: string; timeout_s?: number }) =>
        request<DnsResult>('/api/diagnostics/dns', { method: 'POST', body: JSON.stringify(body) }),

    run: (body: RunBody = {}) =>
        request<DiagnosticsRun>('/api/diagnostics/run', { method: 'POST', body: JSON.stringify(body) }),

    benchmark: (body: BenchmarkBody) =>
        request<DiagnosticsRun>('/api/diagnostics/benchmarks', {
            method: 'POST',
            body: JSON.stringify(body),
        }),

    faults: (body: FaultBody) =>
        request<FaultScenarioResult>('/api/diagnostics/faults', {
            method: 'POST',
            body: JSON.stringify(body),
        }),

    hysteresisSweep: (body: {
        ratios?: number[];
        f_thresh?: number;
        repetitions?: number;
        seed?: number;
        total_intervals?: number;
    } = {}) =>
        request<HysteresisSweep>('/api/diagnostics/hysteresis-sweep', {
            method: 'POST',
            body: JSON.stringify(body),
        }),

    schedulerComparison: (body: {
        paths?: { path_id: string; fd_ms: number; tp_bps: number; loss_rate: number; cwnd_bytes?: number }[];
        payload_bytes?: number;
        chunk_size_bytes?: number;
        repetitions?: number;
        variants?: string[];
        retransmit_policies?: string[];
        seed?: number;
    } = {}) =>
        request<SchedulerComparison>('/api/diagnostics/scheduler-comparison', {
            method: 'POST',
            body: JSON.stringify(body),
        }),

    history: (limit = 50) => request<RunHistory>(`/api/diagnostics/runs?limit=${limit}`),

    runDetail: (id: string) =>
        request<DiagnosticsRun>(`/api/diagnostics/runs/${encodeURIComponent(id)}`),

    compare: (runIds: string[]) =>
        request<RunComparison>('/api/diagnostics/runs/compare', {
            method: 'POST',
            body: JSON.stringify({ run_ids: runIds }),
        }),

    clearHistory: () =>
        request<{ removed: number; count: number }>('/api/diagnostics/runs', { method: 'DELETE' }),

    baselineCapture: (body: {
        phase: 'before' | 'after';
        target?: string;
        count?: number;
        timeout_s?: number;
        repetitions?: number;
        label?: string;
    }) =>
        request<DiagnosticsRun>('/api/diagnostics/integrations/feature-a/capture', {
            method: 'POST',
            body: JSON.stringify(body),
        }),

    baselineCompare: (before: string, after: string) =>
        request<BaselineComparison>('/api/diagnostics/integrations/feature-a/compare', {
            method: 'POST',
            body: JSON.stringify({ before_run_id: before, after_run_id: after }),
        }),

    featureBPair: (diagnosticsRunId: string, transfer: Record<string, unknown>) =>
        request<FeatureBPair>('/api/diagnostics/integrations/feature-b', {
            method: 'POST',
            body: JSON.stringify({ diagnostics_run_id: diagnosticsRunId, transfer }),
        }),

    /** The server streams the export body; read it as text and hand it to a download. */
    exportRun: async (id: string, format: 'json' | 'csv') => {
        const response = await apiFetch(`/api/diagnostics/runs/${encodeURIComponent(id)}/export?format=${format}`, {
            principal: 'diagnostics-lab',
            timeoutMs: 300_000,
        });
        if (!response.ok) {
            let payload: unknown = null;
            try {
                payload = await response.json();
            } catch {
                /* an unparseable error body is not worth a second failure */
            }
            throw new ApiError(describePayload(payload, response.status), response.status, payload);
        }
        return { filename: `${id}.${format}`, content: await response.text() };
    },
};

/** Hand generated content to the browser as a real download. */
export function downloadText(filename: string, content: string, mime = 'text/plain') {
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
