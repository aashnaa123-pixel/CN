/**
 * labs/api.ts — the only place the networking labs talk to the backend.
 *
 * Every panel is populated from a response the API actually returned; nothing
 * here invents a number. Errors carry the server's own explanation (the API
 * returns a readable `detail` for every validation failure).
 */
import { apiRequest, ApiError } from '../api/client';

export { ApiError };

const call = <T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> =>
    apiRequest<T>(path, {
        principal: 'labs',
        timeoutMs: 120_000,
        method,
        body: body === undefined ? undefined : JSON.stringify(body),
    });

// -- shapes (only the fields the UI reads) ---------------------------------

export interface GLink {
    id?: string; a: string; b: string; cost: number;
    delay_ms?: number; loss?: number; bandwidth_mbps?: number; up?: boolean;
}
export interface GGraph { nodes: string[]; links: GLink[] }
export interface TraceStep {
    order: number; node: string; distance: number;
    relaxed: { to: string; via_link: string; old: number | null; new: number; improved: boolean }[];
}
export interface Route {
    source: string; target: string; reachable: boolean; path: string[]; links: string[];
    cost: number | null; visited: string[]; distances: Record<string, number | null>;
    trace: TraceStep[];
}
export interface Alt { path: string[]; links: string[]; cost: number }
export interface Iteration {
    iteration: number;
    selected: string | null;
    visited: string[];
    /** null = infinity (no path found yet) */
    distances: Record<string, number | null>;
    predecessors: Record<string, string | null>;
    relaxed: { to: string; via_link: string; old: number | null; new: number; improved: boolean }[];
    note: string;
}
export interface RoutingRow {
    destination: string;
    cost: number | null;
    next_hop: string | null;
    path: string[];
    status: 'source' | 'reachable' | 'unreachable';
}
export interface ExecutionInfo {
    id: string; feature: string; title: string;
    mode: 'REAL' | 'SIMULATION' | 'UNVERIFIED';
    declared_mode?: string;
    operation: string; implementation: string; evidence: string;
    model?: string | null; limitations: string[];
    executed_at?: string; seed?: number; target?: string; outcome?: string;
    inputs?: Record<string, unknown>;
}
export interface FeatureProfile { mode: 'REAL' | 'SIMULATION' | 'MIXED' | 'UNVERIFIED'; operations: ExecutionInfo[] }
export interface ExecutionProfile { features: Record<string, FeatureProfile>; definitions: Record<string, string> }
export interface SolveResponse {
    route: Route; alternatives: Alt[]; cost_semantics: string;
    iterations: Iteration[]; routing_table: RoutingRow[];
    tie_breaking: string; terminology: string; execution?: ExecutionInfo;
}
export interface WhatIfSide { graph: GGraph; route: Route; alternatives: Alt[] }
export interface WhatIfResponse {
    before: WhatIfSide; after: WhatIfSide; rerouted: boolean;
    cost_delta: number | null; summary: string;
    execution?: ExecutionInfo;
}
export interface Change { kind: 'link_down' | 'link_up' | 'cost_change'; a: string; b: string; cost?: number }

export type Num = Record<string, number | string | boolean | null | undefined>;
export interface TcpResult {
    model: string;
    params: Num;
    summary: Record<string, number | string | boolean | null>;
    cwnd_series: [number, number, number, string, number][];
    throughput_series: [number, number][];
    events: { t: number; type: string; [k: string]: unknown }[];
    assumptions: string[];
    label?: string;
    execution?: ExecutionInfo;
}
export interface TcpCompare {
    runs: TcpResult[]; table: Record<string, number | string>[]; finding: string; assumptions: string[];
    execution?: ExecutionInfo;
}
export interface ShapeSide {
    algorithm: string;
    summary: Record<string, number>;
    departures: [number, number][];
    drops: number[];
    queue_series: [number, number][];
    token_series: [number, number][];
}
export interface ShapeResult {
    params: Num;
    workload: { pattern: string; packets: number; offered_bps: number; shaping_rate_bps: number; overloaded: boolean; arrivals: number[] };
    token_bucket: ShapeSide; leaky_bucket: ShapeSide;
    findings: string[]; assumptions: string[];
    execution?: ExecutionInfo;
}
export interface ProtocolNote { aspect: string; tcp: string; udp: string }
export interface TcpUdpResult {
    mode: 'real_loopback' | 'simulated'; kind: string; label: string;
    params: Num;
    tcp: Record<string, any>; udp: Record<string, any>;
    findings: string[]; limitations: string[]; assumptions?: string[]; method?: string[];
    run_duration_s?: number; environment?: { platform: string; python: string };
    cwnd_series?: TcpResult['cwnd_series']; protocol_notes: ProtocolNote[];
    execution?: ExecutionInfo;
}
export interface Phase {
    phase: string; description: string; reachable: boolean; path: string[];
    path_params?: { hops: number; rtt_ms: number; loss_rate: number; bandwidth_mbps: number };
    metrics: null | {
        completed: boolean; completion_time_s: number | null; goodput_mbps: number;
        retransmissions: number; timeouts: number; max_cwnd_segments: number;
    };
    cwnd_series?: TcpResult['cwnd_series'];
}
export interface IntegrationResult {
    label: string; params: Num; graph: GGraph; heartbeats: boolean[]; states: string[];
    timeline: { tick: number; state: string; action: string; path: string[]; cost: number | null; rerouted: boolean }[];
    phases: Phase[];
    outcome: Record<string, any> & { notes: string[] };
    boundaries: string[]; csv: string;
    execution?: ExecutionInfo;
}

export const labsApi = {
    execution: () => call<ExecutionProfile>('GET', '/api/labs/execution'),
    routingExample: () => call<{ graph: GGraph; source: string; target: string; suggested_change: Change }>('GET', '/api/labs/routing/example'),
    solve: (graph: GGraph, source: string, target: string) =>
        call<SolveResponse>('POST', '/api/labs/routing/solve', { graph, source, target, alternatives: 3 }),
    whatIf: (graph: GGraph, source: string, target: string, changes: Change[]) =>
        call<WhatIfResponse>('POST', '/api/labs/routing/whatif', { graph, source, target, changes, alternatives: 3 }),
    tcp: (params: Record<string, unknown>) => call<TcpResult>('POST', '/api/labs/tcp/simulate', params),
    tcpCompare: (scenarios: Record<string, unknown>[]) => call<TcpCompare>('POST', '/api/labs/tcp/compare', { scenarios }),
    shaping: (params: Record<string, unknown>) => call<ShapeResult>('POST', '/api/labs/shaping/simulate', params),
    tcpUdpSim: (params: Record<string, unknown>) => call<TcpUdpResult>('POST', '/api/labs/tcp-udp/simulate', params),
    tcpUdpReal: (params: Record<string, unknown>) => call<TcpUdpResult>('POST', '/api/labs/tcp-udp/real', params),
    integration: (params: Record<string, unknown>) => call<IntegrationResult>('POST', '/api/labs/integration/run', params),
};
