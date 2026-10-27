/**
 * types.ts — TypeScript mirrors of the backend's response shapes.
 *
 * These are hand-written against apps/api rather than generated, so a field that
 * does not exist on the wire is a type error rather than a runtime surprise.
 * Optional fields are marked optional only where the backend genuinely may omit
 * them, never to paper over a shape that should be fixed server-side.
 */

// --- Topology / Recovery Lab ---------------------------------------------

export type LinkState = 'HEALTHY' | 'SUSPECT' | 'FAILED' | 'UNKNOWN'

export interface NodeCounters {
  sent: number
  received: number
  forwarded: number
  dropped_loop: number
  dropped_no_route: number
  dropped_invalid: number
  route_version: number
  delivered?: number
  acks_received?: number
  acks_accepted?: number
  acks_timed_out?: number
  pending_acks?: number
}

export interface LabNode {
  node_id: string
  port: number
  running: boolean
  failed: boolean
  uptime_s: number
  link_states: Record<string, LinkState>
  routes: Record<string, { next_hop: string; path: string[]; version: number }>
  counters: NodeCounters
}

export interface LabLink {
  link_id: string
  node_a_id: string
  node_b_id: string
  status: 'UP' | 'DOWN'
  state_a: LinkState
  state_b: LinkState
}

export interface FailureRecord {
  target: string
  kind: 'node' | 'link'
  injected_at: number
  detail: string
}

export interface LabTopology {
  running: boolean
  layout?: string
  started_at?: number | null
  uptime_s?: number
  nodes: LabNode[]
  links: LabLink[]
  failures: FailureRecord[]
  message?: string
}

export interface RouteEntry {
  status: 'OK' | 'UNREACHABLE'
  next_hop?: string
  path?: string[]
  version: number
  reason?: string
}

export interface DeliveryResult {
  delivered: boolean
  source: string
  destination: string
  path?: string[]
  reason?: string | null
  ts?: number
}

export interface LabEvent {
  type: string
  payload: Record<string, unknown>
}

// --- Traffic Lab ----------------------------------------------------------

export interface ClassMetrics {
  traffic_class: string
  packets_offered: number
  packets_delivered: number
  packets_dropped: number
  latency_median_ms: number | null
  latency_p95_ms: number | null
  latency_max_ms: number | null
  throughput_bps: number
}

export interface TrafficResult {
  scheduler: string
  workload: Record<string, number>
  link: Record<string, number>
  classes: Record<string, ClassMetrics>
  busy_time_ms: number
  utilisation: number
  simulated: boolean
  generated_at: string
}

export interface TrafficComparison {
  run_id: string
  generated_at: string
  workload: Record<string, number>
  link: Record<string, number>
  results: TrafficResult[]
  findings: string[]
  limitations: string[]
  offered_bps: number
  link_capacity_bps: number
  congested: boolean
  caveat: string
  table: string
}

export interface SchedulerOption {
  id: string
  label: string
  description?: string
}

// --- Transfers ------------------------------------------------------------

export interface PathStats {
  path_id: string
  chunks_sent: number
  retransmissions: number
  share_pct: number
}

export interface TransferResult {
  transfer_id: string
  run_id: string
  scheduler_variant: string
  total_chunks: number
  delivered_chunks: number
  failed_chunks: number
  status: string
  integrity_ok: boolean | null
  source_sha256: string | null
  receiver_sha256: string | null
  duration_s: number
  goodput_bps: number
  retransmission_count: number
  out_of_order_count: number | null
  per_path: PathStats[]
  persisted: boolean
  persistence_error?: string | null
  started_at?: string | null
  ended_at?: string | null
  chunks?: {
    chunk_index: number
    status: string
    path_id: string | null
    attempts: number
  }[]
}

// --- Benchmarks -----------------------------------------------------------

export interface BenchmarkRun {
  run_id: string
  transport: string
  status?: string
  rtt_ms?: number | null
  jitter_ms?: number | null
  loss_rate?: number | null
  throughput_bps?: number | null
  duration_s?: number
  message_size?: number
  simulated?: boolean
  [key: string]: unknown
}

// --- System ---------------------------------------------------------------

export interface SecurityStatus {
  tls: Record<string, unknown>
  emulation: Record<string, unknown>
}

export interface AuditEntry {
  timestamp: string
  principal: string
  action: string
  resource: string
  result: 'ALLOW' | 'DENY'
  detail: Record<string, unknown> | null
}

export interface AuditResponse {
  count: number
  /**
   * Whether audit writes are actually reaching PostgreSQL. An empty list is
   * ambiguous without this: it can mean "no actions" or "recording failed".
   */
  persistence: {
    persist_enabled: boolean
    persist_failures: number
    in_memory_records: number
  }
  entries: AuditEntry[]
}

export interface RunMetrics {
  run_id: string
  count: number
  samples: {
    name: string
    value: number
    unit: string | null
    timestamp: string
    labels: Record<string, unknown>
  }[]
}
