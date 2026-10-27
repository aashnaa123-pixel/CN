/**
 * types.ts — the shapes /api/xfer returns.
 */

export type ChunkStatus = 'PENDING' | 'IN_FLIGHT' | 'DELIVERED' | 'FAILED' | 'RETRYING';
export type TransferState =
    | 'EMPTY' | 'READY' | 'RUNNING' | 'COMPLETE' | 'FAILED' | 'CANCELLED';

export interface LabPath {
    path_id: string;
    fd_estimate_ms: number;
    tp_estimate_bps: number;
    loss_rate: number;
    status: string;
    chunks_sent: number;
    in_flight: number;
    retransmissions: number;
    /** Always "logical": a modelled profile inside the process, not a NIC. */
    kind: string;
}

export interface ChunkView {
    chunk_index: number;
    offset: number;
    size_bytes: number;
    sha256: string;
    status: ChunkStatus;
    path_id: string | null;
    attempts: number;
    arrival_position: number | null;
    rtt_ms: number | null;
}

export interface LabEvent {
    seq: number;
    ts: number;
    transfer_id: string;
    type: string;
    message: string;
    severity: 'info' | 'warning' | 'error';
    detail: Record<string, unknown>;
}

export interface TransferResultView {
    transfer_id: string;
    scheduler_variant: string;
    retransmit_policy: string;
    total_chunks: number;
    delivered_chunks: number;
    failed_chunks: number;
    duration_s: number;
    goodput_bps: number;
    retransmission_count: number;
    out_of_order_count: number;
    source_sha256: string;
    receiver_sha256: string | null;
    /** null/None means "no verdict was reached", distinct from a mismatch. */
    integrity_ok: boolean | null;
    per_path_utilisation: Record<string, number>;
    per_path_retransmissions: Record<string, number>;
}

export interface TransferView {
    transfer_id: string;
    filename: string;
    server_filename: string;
    file_size_bytes: number;
    source_sha256: string;
    chunk_size_bytes: number;
    total_chunks: number;
    scheduler_variant: string;
    retransmit_policy: string;
    max_retries: number;
    state: TransferState;
    counts: {
        pending: number;
        in_flight: number;
        delivered: number;
        failed: number;
        retrying: number;
        delivered_bytes: number;
    };
    progress_pct: number;
    paths: LabPath[];
    out_of_order_count: number;
    retransmission_count: number;
    arrival_order: number[];
    elapsed_s: number;
    goodput_bps: number;
    started_at: number | null;
    ended_at: number | null;
    chunk_enumeration_truncated: boolean;
    result: TransferResultView | null;
}

export interface PathInput {
    path_id: string;
    fd_estimate_ms: number;
    tp_estimate_bps: number;
    loss_rate: number;
}

export interface Option {
    id: string;
    label: string;
    description?: string;
}

export interface LabCapabilities {
    scheduler_variants: Option[];
    retransmit_policies: Option[];
    link_schedulers: Option[];
    limits: { max_file_bytes: number; max_paths: number; chunk_size_range: [number, number] };
    delivery_model: string;
    scheduler_caveat: string;
}

export interface SchedulerComparison {
    run_id: string;
    workload: Record<string, unknown>;
    link: Record<string, unknown>;
    results: SchedulerRunResult[];
    findings: string[];
    limitations: string[];
    offered_bps: number;
    link_capacity_bps: number;
    congested: boolean;
    simulated: true;
    measured_over_real_sockets: false;
    caveat: string;
    table: string;
}

export interface SchedulerRunResult {
    scheduler: string;
    workload: Record<string, unknown>;
    link: Record<string, unknown>;
    classes: Record<string, {
        traffic_class: string;
        packets_offered: number;
        packets_delivered: number;
        packets_dropped: number;
        latency_median_ms: number | null;
        latency_p95_ms: number | null;
        latency_max_ms: number | null;
        throughput_bps: number;
    }>;
    busy_time_ms: number;
    utilisation: number;
    simulated: boolean;
}

export interface VariantRow {
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

export interface VariantComparison {
    run_id: string;
    kind: string;
    workload: Record<string, unknown>;
    environment: Record<string, unknown>;
    rows: VariantRow[];
    findings: string[];
    limitations: string[];
    simulated: true;
    measured_over_real_sockets: false;
    caveat: string;
    table: string;
    csv: string;
}

export interface LabState {
    transfer: TransferView | null;
    chunks: ChunkView[];
    events: LabEvent[];
    comparisons: Record<string, unknown>[];
    delivery_model: string;
    max_file_bytes: number;
    workspace_ttl_s: number;
    state: TransferState;
    running: boolean;
    download_available?: boolean;
}