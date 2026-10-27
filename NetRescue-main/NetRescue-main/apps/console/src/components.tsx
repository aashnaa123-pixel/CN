/**
 * components.tsx — shared presentation pieces.
 *
 * Status is never carried by colour alone: every Pill includes its label as
 * text, and every empty state explains what is missing and what to do.
 */
import type { CSSProperties, ReactNode } from 'react'
import type { LabEvent, LinkState } from './types'

// ---------------------------------------------------------------------------
// Status
// ---------------------------------------------------------------------------

export type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'idle'

export function Pill({
  tone = 'idle',
  children,
  pulse = false,
}: {
  tone?: Tone
  children: ReactNode
  pulse?: boolean
}) {
  return (
    <span className={`pill ${tone}`}>
      <span className={`dot${pulse ? ' pulse' : ''}`} />
      {children}
    </span>
  )
}

/** Map a lab link state to a tone. UNKNOWN is deliberately not 'ok'. */
export function linkTone(state: LinkState): Tone {
  switch (state) {
    case 'HEALTHY':
      return 'ok'
    case 'SUSPECT':
      return 'warn'
    case 'FAILED':
      return 'bad'
    default:
      return 'idle'
  }
}

export function SocketBadge({ state }: { state: string }) {
  const tone: Tone =
    state === 'open' ? 'ok' : state === 'connecting' ? 'warn' : 'bad'
  const label =
    state === 'open' ? 'Live' : state === 'connecting' ? 'Connecting' : 'Disconnected'
  return <Pill tone={tone} pulse={state === 'open'}>{label}</Pill>
}

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

export function Card({
  title,
  hint,
  right,
  children,
}: {
  title: string
  hint?: ReactNode
  right?: ReactNode
  children?: ReactNode
}) {
  return (
    <section className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, alignItems: 'flex-start' }}>
        <div style={{ minWidth: 0 }}>
          <h3>{title}</h3>
          {hint && <p className="card-hint">{hint}</p>}
        </div>
        {right && <div style={{ flexShrink: 0 }}>{right}</div>}
      </div>
      {children}
    </section>
  )
}

export function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: Tone
}) {
  const colour =
    tone === 'ok' ? 'var(--ok)'
    : tone === 'bad' ? 'var(--bad)'
    : tone === 'warn' ? 'var(--warn)'
    : undefined
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value" style={colour ? { color: colour } : undefined}>
        {value}
      </div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  )
}

export function Empty({
  icon = '○',
  title,
  hint,
  action,
}: {
  icon?: string
  title: string
  hint?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="empty">
      <span className="big">{icon}</span>
      <div style={{ color: 'var(--text-dim)', fontWeight: 500 }}>{title}</div>
      {hint && <div style={{ marginTop: 6, maxWidth: 480, marginInline: 'auto' }}>{hint}</div>}
      {action && <div style={{ marginTop: 14 }}>{action}</div>}
    </div>
  )
}

export function Message({
  kind,
  children,
}: {
  kind: 'error' | 'ok' | 'info' | 'warn'
  children: ReactNode
}) {
  const icon = kind === 'error' ? '✕' : kind === 'ok' ? '✓' : kind === 'warn' ? '!' : 'i'
  return (
    <div className={`msg ${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      <span aria-hidden="true">{icon}</span>
      <span>{children}</span>
    </div>
  )
}

export function Loading({ label = 'Loading' }: { label?: string }) {
  return (
    <div style={{ display: 'flex', gap: 9, alignItems: 'center', color: 'var(--text-faint)', fontSize: 13, padding: '14px 0' }}>
      <span className="spinner" />
      {label}…
    </div>
  )
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ width: `${100 - i * 12}%` }} />
      ))}
    </div>
  )
}

/** Banner for features that cannot run here, stated as a limitation. */
export function Unavailable({ what, why, fix }: { what: string; why: ReactNode; fix?: ReactNode }) {
  return (
    <Empty
      icon="⊘"
      title={`${what} is unavailable on this host`}
      hint={
        <>
          {why}
          {fix && <div style={{ marginTop: 10 }}>{fix}</div>}
        </>
      }
    />
  )
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

export function fmtBps(bps: number | null | undefined, digits = 2): string {
  if (bps === null || bps === undefined || Number.isNaN(bps)) return '—'
  if (bps === 0) return '0 bps'
  if (bps < 1000) return `${bps.toFixed(0)} bps`
  if (bps < 1e6) return `${(bps / 1e3).toFixed(digits)} kbps`
  if (bps < 1e9) return `${(bps / 1e6).toFixed(digits)} Mbps`
  return `${(bps / 1e9).toFixed(digits)} Gbps`
}

export function fmtMs(ms: number | null | undefined, digits = 2): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '—'
  if (ms < 1) return `${(ms * 1000).toFixed(0)} µs`
  if (ms < 1000) return `${ms.toFixed(digits)} ms`
  return `${(ms / 1000).toFixed(digits)} s`
}

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KiB`
  return `${(n / 1024 ** 2).toFixed(2)} MiB`
}

export function fmtPct(p: number | null | undefined, digits = 1): string {
  if (p === null || p === undefined || Number.isNaN(p)) return '—'
  return `${p.toFixed(digits)}%`
}

export function fmtTime(ts: number | string | null | undefined): string {
  if (ts === null || ts === undefined) return '—'
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleTimeString(undefined, { hour12: false })
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 1) return `${(seconds * 1000).toFixed(0)} ms`
  if (seconds < 60) return `${seconds.toFixed(2)} s`
  const m = Math.floor(seconds / 60)
  return `${m}m ${(seconds % 60).toFixed(0)}s`
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

/** Classify an event for colouring. Severity is only a hint; the text stands alone. */
export function eventTone(type: string): string {
  if (/fail|invalid|unreachable|drop|timeout|forg|reject/i.test(type)) return 'bad'
  if (/suspect|retrans|retry|warn|degrad/i.test(type)) return 'warn'
  if (/deliver|complete|started|restored|healthy/i.test(type)) return 'ok'
  return ''
}

export function EventLog({
  events,
  limit = 60,
  emptyHint = 'Events will appear here as the topology changes.',
}: {
  events: LabEvent[]
  limit?: number
  emptyHint?: string
}) {
  if (!events.length) return <Empty icon="⌁" title="No events yet" hint={emptyHint} />
  return (
    <div className="events">
      {events.slice(0, limit).map((e, i) => (
        <div key={`${e.type}-${i}`} className={`event ${eventTone(e.type)}`}>
          <span className="t">{fmtTime(nowOf(e))}</span>
          <span className="type">{e.type}</span>
          <span className="detail">{summarise(e.payload)}</span>
        </div>
      ))}
    </div>
  )
}

function nowOf(e: LabEvent): number {
  const ts = e.payload?.ts
  return typeof ts === 'number' ? ts : Date.now() / 1000
}

/** One short line per event, pulling out the fields an operator cares about. */
function summarise(payload: Record<string, unknown>): string {
  const skip = new Set(['severity', 'ts', 'node_id', 'kind'])
  const parts: string[] = []
  for (const [k, v] of Object.entries(payload)) {
    if (skip.has(k) || v === null || v === undefined) continue
    let text: string
    if (Array.isArray(v)) text = v.join('→')
    else if (typeof v === 'object') text = JSON.stringify(v)
    else text = String(v)
    if (text.length > 90) text = text.slice(0, 88) + '…'
    parts.push(`${k}=${text}`)
    if (parts.length >= 5) break
  }
  return parts.join('  ') || '—'
}

// ---------------------------------------------------------------------------
// Bar chart
// ---------------------------------------------------------------------------

/**
 * A plain horizontal bar chart.
 *
 * Values come from the caller — this component never generates, smooths or pads
 * data. A null value renders as an explicit "no data" marker rather than a zero
 * bar, because zero and unmeasured are different claims.
 */
export function BarChart({
  data,
  unit = '',
  height = 26,
  color = 'var(--accent)',
  log = false,
}: {
  data: { label: string; value: number | null; color?: string }[]
  unit?: string
  height?: number
  color?: string
  log?: boolean
}) {
  if (!data.length) return <Empty icon="▤" title="Nothing to chart yet" />

  const positive = data
    .map((d) => d.value)
    .filter((v): v is number => v !== null && Number.isFinite(v) && v > 0)
  const max = positive.length ? Math.max(...positive) : 1
  const scale = (v: number) =>
    log ? Math.log10(1 + v) / Math.log10(1 + max) : v / max

  return (
    <div style={{ display: 'grid', gap: 6 }}>
      {data.map((d) => {
        const pct = d.value === null || d.value <= 0 ? 0 : scale(d.value) * 100
        return (
          <div key={d.label} style={{ display: 'grid', gridTemplateColumns: '150px 1fr auto', gap: 10, alignItems: 'center' }}>
            <div style={{ fontSize: 12, color: 'var(--text-dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={d.label}>
              {d.label}
            </div>
            <div style={{ background: 'var(--bg-input)', borderRadius: 4, height, overflow: 'hidden' }}>
              {d.value === null ? (
                <div style={{ padding: '0 8px', fontSize: 11, color: 'var(--text-faint)', lineHeight: `${height}px` }}>
                  not measured
                </div>
              ) : (
                <div
                  className="bar"
                  style={{
                    width: `${pct}%`,
                    height: '100%',
                    background: d.color ?? color,
                    borderRadius: 4,
                    minWidth: d.value > 0 ? 2 : 0,
                  }}
                />
              )}
            </div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 12, minWidth: 96, textAlign: 'right' }}>
              {d.value === null ? '—' : `${formatNumber(d.value)}${unit}`}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function formatNumber(v: number): string {
  if (!Number.isFinite(v)) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e6) return `${(v / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `${(v / 1e3).toFixed(1)}k`
  if (abs < 1 && abs > 0) return v.toPrecision(3)
  return v.toFixed(abs < 10 ? 2 : 0)
}

export function Divider({ label }: { label?: string }) {
  const style: CSSProperties = {
    display: 'flex', alignItems: 'center', gap: 10,
    margin: '18px 0 14px', color: 'var(--text-faint)',
    fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.07em',
  }
  return (
    <div style={style}>
      {label}
      <span style={{ flex: 1, height: 1, background: 'var(--border)' }} />
    </div>
  )
}
