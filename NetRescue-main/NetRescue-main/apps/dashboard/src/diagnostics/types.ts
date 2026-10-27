/**
 * types.ts — the shapes the Feature 3 diagnostics lab reads from the API.
 *
 * Every result carries a `data_kind`:
 *
 *   real         measured on this machine's network just now
 *   controlled   a benchmark or sweep over modelled conditions
 *   simulated    produced by the seeded fault simulator
 *   unsupported  the host cannot provide this, and the reason is included
 *
 * The UI must render that label next to any number it shows. A metric without a
 * provenance label is a metric the reader cannot judge.
 */

export type DataKind = 'real' | 'controlled' | 'simulated' | 'unsupported';
export type RunStatus = 'DONE' | 'PARTIAL' | 'FAILED' | string;
export type SectionStatus = 'ok' | 'error' | 'skipped';

// --- capabilities -----------------------------------------------------------

export interface Capability {
    supported: boolean;
    command?: string;
    reason?: string;
    hint?: string;
    linux_reference?: string;
    note?: string;
}

export interface DiagnosticsStatus {
    platform: string;
    release: string;
    is_windows: boolean;
    real_measurements: Record<string, Capability>;
    controlled_simulation: Capability;
    unsupported: Record<string, Capability>;
    timestamp: number;
    suggested_target: string;
    history: { stored_runs: number; max_history: number; note: string };
    notes?: string[];
}

export interface ScenarioDescriptor {
    name: string;
    description: string;
}

export interface ScenariosResponse {
    scenarios: ScenarioDescriptor[];
    seed_control: string;
    all_simulated: boolean;
}

// --- real probes ------------------------------------------------------------

export interface PingResult {
    target: string;
    command: string[];
    sent: number;
    received: number;
    loss_rate: number;
    loss_pct: string;
    samples_ms: number[];
    min_ms: number | null;
    avg_ms: number | null;
    max_ms: number | null;
    stddev_ms: number | null;
    sub_millisecond_samples: number;
    duration_s: number;
    units: string;
    reachable: boolean;
    timestamp: number;
    notes: string[];
}

export interface TraceHop {
    hop: number;
    node: string;
    rtts_ms: number[];
    rtt_ms: number | null;
    timed_out: boolean;
    sub_millisecond: boolean;
}

export interface TracerouteResult {
    target: string;
    hops: TraceHop[];
    hop_count: number;
    max_hops: number;
    units: string;
    duration_s: number;
    command: string[];
    timestamp: number;
    notes: string[];
}

export interface DnsResult {
    name: string;
    resolver: string;
    addresses: string[];
    ipv4: string[];
    ipv6: string[];
    duration_ms: number | null;
    units: string;
    resolved: boolean;
    timestamp: number;
    notes: string[];
}

export interface Adapter {
    name: string | null;
    description: string | null;
    status: string | null;
    link_speed: string | null;
    mac_address: string | null;
    if_index: number | null;
}

export interface AdaptersResult {
    available: boolean;
    count?: number;
    up_count?: number;
    adapters: Adapter[];
    reason?: string;
    hint?: string;
    source?: string;
    timestamp?: number;
    cached?: boolean;
    cache_ttl_s?: number;
    notes?: string[];
}

export interface NetworkInterface {
    alias: string;
    if_index: number | null;
    ipv4_addresses: string[];
    prefix_length?: number | null;
    default_gateway: string | null;
    dns_servers: string[];
}

export interface IpConfigurationResult {
    available: boolean;
    default_gateway?: string | null;
    interfaces: NetworkInterface[];
    reason?: string;
    hint?: string;
    source?: string;
    timestamp?: number;
    cached?: boolean;
    cache_ttl_s?: number;
    notes?: string[];
}

export interface NetworkStatus {
    platform?: string;
    release?: string;
    machine?: string;
    hostname?: string;
    python?: string;
    windows?: boolean;
    is_windows?: boolean;
    default_gateway?: string | null;
    adapters: AdaptersResult;
    ip_configuration: IpConfigurationResult;
    timestamp?: number;
    notes?: string[];
}

// --- the composed run -------------------------------------------------------

export interface SectionEnvelope<T> {
    status: SectionStatus;
    data?: T;
    reason?: string;
    error?: string;
    kind?: string;
    hint?: string;
}

export interface RunAggregate {
    sent: number | null;
    received: number | null;
    lost: number | null;
    packet_loss_rate: number | null;
    min_ms: number | null;
    avg_ms: number | null;
    max_ms: number | null;
    stddev_ms: number | null;
    median_ms?: number | null;
    packet_loss_pct?: string;
    samples?: number;
}

export interface RunEnvironment {
    os?: string;
    release?: string;
    hostname?: string;
    machine?: string;
    python?: string;
    software_version?: string;
}

export interface DiagnosticsRun {
    run_id: string;
    kind: string;
    source?: string;
    simulated: boolean;
    data_kind: DataKind;
    status: RunStatus;
    config: Record<string, unknown>;
    config_hash: string;
    software_version: string;
    started_at: number | string;
    generated_at?: number | string;
    duration_s?: number;
    sections: {
        ping?: SectionEnvelope<PingResult>;
        network_status?: SectionEnvelope<NetworkStatus>;
        traceroute?: SectionEnvelope<TracerouteResult>;
        dns?: SectionEnvelope<DnsResult>;
    };
    ok_sections: string[];
    error_sections: string[];
    aggregate: RunAggregate;
    environment?: RunEnvironment;
    limitations: string[];
    errors?: string[];
    persisted?: boolean;
    /** present on benchmark runs (not the composed bundle) */
    repetitions?: RepetitionOutcome[];
    successful_repetitions?: number;
    failed_repetitions?: number;
    phase?: 'before' | 'after';
    annotation?: {
        source?: string;
        data_kind?: DataKind;
        label?: string;
        feature_a_relationship?: string;
    };
}

export interface RepetitionOutcome {
    index: number;
    ok: boolean;
    sent: number;
    received: number;
    lost: number;
    loss_rate: number | null;
    min_ms: number | null;
    avg_ms: number | null;
    max_ms: number | null;
    stddev_ms: number | null;
    samples_ms: number[];
    duration_s: number;
    error: string | null;
}

// --- deterministic fault simulator ------------------------------------------

export interface ProbeEvent {
    index: number;
    ok: boolean;
    latency_ms: number | null;
    state: 'HEALTHY' | 'UNHEALTHY';
}

export interface FaultTransition {
    index: number;
    from: string;
    to: string;
    latency_ms: number | null;
}

export interface FaultScenarioResult {
    run_id: string;
    kind: string;
    scenario: string;
    seed: number;
    simulated: boolean;
    data_kind: DataKind;
    config: Record<string, unknown>;
    config_hash: string;
    software_version: string;
    generated_at: number;
    probes: ProbeEvent[];
    total_probes: number;
    healthy_count: number;
    unhealthy_count: number;
    transitions: FaultTransition[];
    transition_count: number;
    benchmark?: DiagnosticsRun | null;
    limitations: string[];
}

// --- history, comparison, reports -------------------------------------------

export interface RunSummary {
    run_id: string;
    kind: string;
    scenario: string | null;
    target: string | null;
    status: RunStatus;
    simulated: boolean;
    data_kind: DataKind;
    config_hash: string;
    generated_at: number | string | null;
    sent: number | null;
    received: number | null;
    lost: number | null;
    packet_loss_pct: string | null;
    avg_ms: number | null;
    min_ms: number | null;
    max_ms: number | null;
    stddev_ms: number | null;
    persisted: boolean;
}

export interface RunHistory {
    runs: RunSummary[];
    count: number;
    max_history: number;
    note: string;
}

export interface ComparisonRow {
    run_id: string;
    data_kind: DataKind;
    kind: string;
    scenario: string | null;
    target: string | null;
    status: RunStatus | null;
    config_hash: string;
    software_version: string;
    repetitions: number;
    sent: number | null;
    received: number | null;
    packet_loss_pct: string | null;
    min_ms: number | null;
    avg_ms: number | null;
    max_ms: number | null;
    stddev_ms: number | null;
    median_ms: number | null;
}

export interface RunComparison {
    runs: ComparisonRow[];
    run_count: number;
    data_kinds: DataKind[];
    notes: string[];
    generated_at_utc: string;
}

export interface MeasurementReport {
    report_format: string;
    report_version: number;
    run_id: string;
    data_kind: DataKind;
    status: RunStatus | null;
    configuration: Record<string, unknown>;
    config_hash: string;
    software_version: string;
    environment: Record<string, unknown>;
    results: {
        sent: number | null;
        received: number | null;
        lost: number | null;
        packet_loss_pct: string | null;
        min_ms: number | null;
        avg_ms: number | null;
        max_ms: number | null;
        stddev_ms: number | null;
        median_ms: number | null;
        units: { latency: string; loss: string };
    };
    findings: string[];
    errors: string[];
    limitations: string[];
    generated_at_utc: string;
}

// --- harnesses --------------------------------------------------------------

export interface SweepPoint {
    ratio: number;
    f_thresh: number;
    r_thresh: number;
    n: number;
    flap_count: number;
    recovery_intervals: number | null;
    recovery_s: number | null;
    detection_intervals: number | null;
    detection_s: number | null;
}

export interface ParetoPoint {
    ratio: number;
    recovery_s: number;
    flap_count: number;
}

export interface HysteresisSweep {
    run_id: string;
    generated_at: string;
    workload: {
        f_thresh: number;
        ratios: number[];
        naive_threshold: number;
        total_intervals: number;
        outage: number[];
        flap_probability: number;
        repetitions: number;
        heartbeat_interval_s: number;
    };
    points: SweepPoint[];
    naive: { flap_count: number; recovery_s: number | null };
    pareto_frontier: ParetoPoint[];
    findings: string[];
    limitations: string[];
    data_kind: DataKind;
    simulated: boolean;
    harness: string;
    config_hash: string;
    software_version: string;
    provenance: string;
}

export interface SchedulerRow {
    variant: string;
    retransmit_policy: string;
    repetitions: number;
    duration_s_median: number | null;
    duration_s_p95: number | null;
    goodput_bps_median: number | null;
    out_of_order_median: number | null;
    out_of_order_p95: number | null;
    retransmissions_median: number | null;
    failed_chunks_median: number | null;
    integrity_ok: string;
    per_path_utilisation: Record<string, number>;
}

export interface SchedulerComparison {
    run_id: string;
    kind: string;
    generated_at: string;
    workload: {
        payload_bytes: number;
        chunk_size_bytes: number;
        chunks: number;
        repetitions: number;
        paths: { path_id: string; fd_ms: number; tp_bps: number; loss_rate: number }[];
        delay_heterogeneity: string;
    };
    environment: Record<string, unknown>;
    rows: SchedulerRow[];
    findings: string[];
    limitations: string[];
    data_kind: DataKind;
    simulated: boolean;
    harness: string;
    config_hash: string;
    software_version: string;
    provenance: string;
}

// --- Feature A / B integration ----------------------------------------------

export interface BaselineComparison {
    kind: string;
    before: { run_id: string; phase?: string; config_hash: string; aggregate: RunAggregate };
    after: { run_id: string; phase?: string; config_hash: string; aggregate: RunAggregate };
    delta: Record<string, { before: number; after: number; delta_ms?: number; delta?: number; delta_pct_points?: string } | null>;
    same_configuration: boolean;
    interpretation: string;
    generated_at_utc: string;
}

export interface FeatureBPair {
    kind: string;
    diagnostics: {
        label: string;
        data_kind: DataKind;
        run_id: string;
        target: string | null;
        config_hash: string;
        metrics: Record<string, number | string | null>;
        units: { latency: string; loss: string };
    };
    transfer: {
        label: string;
        data_kind: DataKind;
        run_id: string | null;
        scheduler_variant: string | null;
        retransmit_policy: string | null;
        metrics: Record<string, number | string | boolean | null | Record<string, number>>;
        units: { goodput: string; duration: string };
    };
    notes: string[];
    generated_at_utc: string;
}
