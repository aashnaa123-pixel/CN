/**
 * types.ts — the shapes /api/playground returns.
 *
 * Written out rather than inferred so a change on the server shows up here as a
 * compile error instead of as `undefined` at runtime in a panel nobody is
 * testing.
 */

export type NodeRole = 'source' | 'router' | 'destination';
export type LinkState = 'HEALTHY' | 'SUSPECT' | 'FAILED' | 'UNKNOWN';

export interface DrawnNode {
    node_id: string;
    label: string;
    role: NodeRole;
    x: number;
    y: number;
}

export interface DrawnLink {
    node_a: string;
    node_b: string;
}

export interface TopologyPayload {
    nodes: DrawnNode[];
    links: DrawnLink[];
    source?: string | null;
    destination?: string | null;
}

export interface ValidationIssue {
    code: string;
    message: string;
    target?: string;
    endpoint?: string;
    /** Present on link findings: how bad it is, not what it looks like. */
    severity?: 'failed' | 'suspect' | 'info';
}

export interface ValidationReport {
    valid: boolean;
    routable: boolean;
    source: string | null;
    destination: string | null;
    primary_path: string[] | null;
    alternate_path: string[] | null;
    path_count: number;
    has_alternate: boolean;
    live_path: string[] | null;
    live_path_count: number;
    connected_nodes: string[];
    errors: ValidationIssue[];
    warnings: ValidationIssue[];
    summary: string;
}

export interface DetectorReading {
    link_id: string;
    state: LinkState;
    f_thresh: number;
    n: number;
    r_thresh: number;
    hysteresis_ratio: number;
    use_ewma: boolean;
    consecutive_misses: number;
    consecutive_successes: number;
    route_flap_count: number;
}

export interface RouteEntry {
    next_hop: string;
    path: string[];
    version: number;
}

export interface NodeCounters {
    sent: number;
    received: number;
    forwarded: number;
    dropped_loop: number;
    dropped_no_route: number;
    dropped_invalid: number;
    route_version: number;
    delivered: number;
    acks_received: number;
    acks_accepted: number;
    acks_timed_out: number;
    pending_acks: number;
}

export interface CanvasNode extends DrawnNode {
    running: boolean;
    failed: boolean;
    port: number | null;
    state: 'UP' | 'DOWN' | 'IDLE';
    detector: Record<string, DetectorReading> | null;
    routes: Record<string, RouteEntry>;
    counters: NodeCounters | null;
}

export interface CanvasLink {
    link_id: string;
    node_a: string;
    node_b: string;
    enabled: boolean;
    status: 'UP' | 'DOWN';
    in_route: boolean;
    state_a: LinkState;
    state_b: LinkState;
    issues: ValidationIssue[];
    notes: ValidationIssue[];
}

export interface ExperimentEvent {
    seq: number;
    ts: number;
    category: string;
    message: string;
    severity: 'info' | 'warning' | 'error';
    detail: Record<string, unknown>;
}

export interface DetectorTransition {
    link_id: string;
    node_id: string;
    from_state: LinkState;
    to_state: LinkState;
    route_flap: boolean;
    ts: number;
}

export interface DeliveryResult {
    delivered: boolean;
    source: string;
    destination: string;
    path?: string[];
    reason?: string | null;
    route_after?: string[] | null;
    route_version?: number;
}

export interface DetectorComparison {
    link_id: string;
    sequence: string[];
    hysteresis: {
        detector: string;
        f_thresh: number;
        n: number;
        r_thresh: number;
        hysteresis_ratio: number;
        use_ewma: boolean;
        transitions: { from: string; to: string; route_flap: boolean }[];
        route_flap_count: number;
        final_state: string;
    };
    naive: {
        detector: string;
        threshold: number;
        transitions: { from: string; to: string; route_flap: boolean }[];
        route_flap_count: number;
        final_state: string;
    };
    route_flaps_saved: number;
    timeline: {
        index: number;
        heartbeat: string;
        hysteresis_state: string;
        naive_state: string;
        hysteresis_flaps: number;
        naive_flaps: number;
        changed: boolean;
    }[];
    note: string;
}

export interface PlaygroundState {
    topology: { nodes: DrawnNode[]; links: { link_id: string; node_a: string; node_b: string }[] };
    validation: ValidationReport;
    running: boolean;
    draft_is_running: boolean;
    source: string | null;
    destination: string | null;
    nodes: CanvasNode[];
    links: CanvasLink[];
    route: string[] | null;
    previous_route: string[] | null;
    route_changes: number;
    packets: { sent: number; delivered: number; failed: number };
    last_delivery: DeliveryResult | null;
    detectors: Record<string, Record<string, DetectorReading>>;
    transitions: DetectorTransition[];
    events: ExperimentEvent[];
    experiments: DetectorComparison[];
    max_nodes: number;
    roles: NodeRole[];
    message?: string;
}