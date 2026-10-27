/**
 * api.ts — the single place the dashboard talks to the backend.
 *
 * Three rules this module enforces:
 *
 *  1. A response is only ever resolved after the backend confirms it. There is
 *     no optimistic success path anywhere in here.
 *  2. Errors carry the server's own explanation. The UI shows the detail string
 *     rather than a generic "something went wrong".
 *  3. The base URL comes from the environment, never from a hardcoded host.
 */
import type {
  AuditResponse,
  DeliveryResult,
  LabTopology,
  RouteEntry,
  RunMetrics,
  SchedulerOption,
  SecurityStatus,
  TrafficComparison,
  TrafficResult,
  TransferResult,
} from './types'

/**
 * The API base URL, from the environment.
 *
 * Two forms are supported:
 *   - absolute, e.g. http://127.0.0.1:8000 — the usual local dev setup
 *   - relative, e.g. /api — the Docker deployment, where nginx proxies to the
 *     API container. A relative base keeps the browser talking to its own origin,
 *     which avoids CORS entirely and works without knowing the container name.
 */
const RAW_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000').replace(
  /\/$/,
  '',
)

export const IS_RELATIVE_BASE = RAW_BASE.startsWith('/')

const BASE = RAW_BASE

/** WebSocket URL for /ws/events, derived from the same base. */
export function eventsUrl(): string {
  if (IS_RELATIVE_BASE) {
    const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${scheme}//${window.location.host}${BASE}/ws/events`
  }
  return `${BASE.replace(/^http/, 'ws')}/ws/events`
}

/** An API failure with the server's explanation attached. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function authHeaders(): Record<string, string> {
  // The prototype binds to loopback and identifies the caller by header
  // (apps/api/deps.py). A missing role falls back to `readonly`, which can read
  // but not act — so these are explicit rather than assumed.
  return {
    'X-NetRescue-Principal': import.meta.env.VITE_PRINCIPAL ?? 'operator',
    'X-NetRescue-Role': import.meta.env.VITE_ROLE ?? 'operator',
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        ...authHeaders(),
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init.headers as Record<string, string> | undefined),
      },
    })
  } catch (cause) {
    // `fetch` throws rather than returning a response when the request never
    // completes — most often because the server is down, but also when CORS
    // rejects the origin, which looks identical from here. Distinguishing them
    // matters: "the API is not running" sends an operator to restart a service
    // that is already running, when the real fix is an origin or a restart of
    // the dev server.
    const crossOrigin = !IS_RELATIVE_BASE && window.location.origin !== new URL(BASE).origin
    throw new ApiError(
      crossOrigin
        ? 'The request to the NetRescue API was blocked before it reached the ' +
          `server. This usually means CORS: the API only allows loopback origins ` +
          `by default, and this page is served from ${window.location.origin}. ` +
          `Set CORS_ORIGINS on the API, or serve the console behind the same ` +
          `origin.`
        : `Could not reach the NetRescue API at ${BASE}. Is it running? ` +
          `Start it with: uvicorn apps.api.main:app --port 8000`,
      0,
      cause,
    )
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!response.ok) {
    throw new ApiError(
      describeError(response.status, payload),
      response.status,
      payload,
    )
  }
  return payload as T
}

function describeError(status: number, payload: unknown): string {
  const detail =
    payload && typeof payload === 'object' && 'detail' in payload
      ? (payload as { detail: unknown }).detail
      : payload

  if (status === 403) {
    const who = 'This action needs the operator role. Send X-NetRescue-Role: operator.'
    return typeof detail === 'string' ? `${detail} — ${who}` : who
  }
  if (status === 409) {
    return typeof detail === 'string'
      ? detail
      : 'The resource is in a state that does not allow this action.'
  }
  if (status === 503) {
    return typeof detail === 'string'
      ? detail
      : 'The database is unavailable, so this needs PostgreSQL to be running.'
  }
  if (typeof detail === 'string' && detail) return detail
  return `Request failed with HTTP ${status}.`
}

// ---------------------------------------------------------------------------
// Recovery Lab
// ---------------------------------------------------------------------------

export const labApi = {
  topology: () => request<LabTopology>('/api/lab/topology'),

  startTopology: (nodes: string[], layout: string, heartbeat = 1.0) =>
    request<{ message: string; topology: LabTopology }>('/api/lab/topology', {
      method: 'POST',
      body: JSON.stringify({ nodes, layout, heartbeat_interval_s: heartbeat }),
    }),

  stopTopology: () =>
    request<{ message: string }>('/api/lab/topology', { method: 'DELETE' }),

  routes: () =>
    request<{ routes: Record<string, Record<string, RouteEntry>> }>(
      '/api/lab/routes',
    ),

  fail: (kind: 'node' | 'link', target: string) =>
    request<{ message: string; topology: LabTopology }>('/api/lab/failures', {
      method: 'POST',
      body: JSON.stringify({ kind, target }),
    }),

  clearFailures: () =>
    request<{ message: string; topology: LabTopology }>(
      '/api/lab/failures/clear',
      { method: 'POST' },
    ),

  /** Resolves only when the backend has confirmed delivery, or says it did not. */
  deliver: (source: string, destination: string, body = 'netrescue-probe') =>
    request<DeliveryResult>('/api/lab/deliver', {
      method: 'POST',
      body: JSON.stringify({ source, destination, body, timeout_s: 5 }),
    }),

  history: () =>
    request<{ deliveries: DeliveryResult[]; events: { type: string }[] }>(
      '/api/lab/history',
    ),
}

// ---------------------------------------------------------------------------
// Traffic Lab
// ---------------------------------------------------------------------------

export interface WorkloadInput {
  critical_rate_hz: number
  critical_size_bits: number
  bulk_rate_hz: number
  bulk_size_bits: number
  duration_ms: number
  max_queue: number
  seed: number
}

export interface LinkInput {
  rate_bps: number
  propagation_ms: number
  max_queue: number
}

export const trafficApi = {
  schedulers: () =>
    request<{
      schedulers: SchedulerOption[]
      traffic_classes: SchedulerOption[]
      disclaimer: string
    }>('/api/traffic/schedulers'),

  compare: (workload: Partial<WorkloadInput>, link: Partial<LinkInput>) =>
    request<TrafficComparison>('/api/traffic/compare', {
      method: 'POST',
      body: JSON.stringify({ workload, link }),
    }),

  run: (scheduler: string, workload: Partial<WorkloadInput>, link: Partial<LinkInput>) =>
    request<{
      run_id: string
      result: TrafficResult
      offered_bps: number
      link_capacity_bps: number
      congested: boolean
      caveat: string
    }>('/api/traffic/run', {
      method: 'POST',
      body: JSON.stringify({ scheduler, workload, link }),
    }),
}

// ---------------------------------------------------------------------------
// Transfers
// ---------------------------------------------------------------------------

export interface PathInput {
  path_id: string
  fd_estimate_ms: number
  tp_estimate_bps: number
  loss_rate: number
}

export const transferApi = {
  variants: () =>
    request<{
      scheduler_variants: SchedulerOption[]
      retransmit_policies: SchedulerOption[]
    }>('/api/transfers/variants'),

  run: (input: {
    run_id: string
    data_b64: string
    scheduler_variant: string
    chunk_size_bytes: number
    max_retries: number
    paths: PathInput[]
  }) =>
    request<TransferResult>('/api/transfers', {
      method: 'POST',
      body: JSON.stringify(input),
    }),

  list: () => request<TransferResult[]>('/api/transfers'),

  detail: (id: string) => request<TransferResult>(`/api/transfers/${id}`),
}

/** Read a File into base64 so the transfer is a real upload of the real bytes. */
export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`))
    reader.onload = () => {
      const result = reader.result as string
      // Strip the data: URL prefix so the server receives raw base64.
      resolve(result.slice(result.indexOf(',') + 1))
    }
    reader.readAsDataURL(file)
  })
}

// ---------------------------------------------------------------------------
// System
// ---------------------------------------------------------------------------

export const systemApi = {
  health: () => request<{ status: string; version: string }>('/health'),
  ready: () => request<{ status: string }>('/ready'),
  security: () => request<SecurityStatus>('/api/security/status'),
  audit: (limit = 100) => request<AuditResponse>(`/api/audit?limit=${limit}`),
  runMetrics: (runId: string) =>
    request<RunMetrics>(`/api/runs/${encodeURIComponent(runId)}/metrics`),
  exportCsv: (runId: string) =>
    request<{ run_id: string; filename: string; content: string; rows: number }>(
      `/api/runs/${encodeURIComponent(runId)}/export.csv`,
    ),
}

export const benchmarkApi = {
  run: (body: Record<string, unknown>) =>
    request<Record<string, unknown>>('/api/benchmarks', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  get: (runId: string) =>
    request<Record<string, unknown>>(`/api/benchmarks/${encodeURIComponent(runId)}`),
  schedulerComparison: (body: Record<string, unknown>) =>
    request<Record<string, unknown>>('/api/benchmarks/scheduler-comparison', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}

export { BASE as API_BASE }
