/**
 * Traffic Lab — FIFO versus priority scheduling.
 *
 * The screen shows the trade-off, not a victory. Critical latency and bulk
 * throughput are always presented together, and the backend's findings are shown
 * verbatim — including when priority did *not* help. Results are labelled
 * queueing-model, never as network measurements.
 */
import { useState } from 'react'
import { trafficApi, type LinkInput, type WorkloadInput } from '../api'
import { useAction, useAsync } from '../hooks'
import {
  BarChart,
  Card,
  Divider,
  Empty,
  Message,
  Pill,
  Stat,
  fmtBps,
  fmtMs,
} from '../components'
import type { TrafficComparison } from '../types'

const DEFAULTS: WorkloadInput & LinkInput = {
  critical_rate_hz: 40,
  critical_size_bits: 1600,
  bulk_rate_hz: 200,
  bulk_size_bits: 40000,
  duration_ms: 500,
  max_queue: 200,
  seed: 4242,
  rate_bps: 5_000_000,
  propagation_ms: 1,
}

const PRESETS = [
  { label: 'Mild congestion (1.6×)', bulk_rate_hz: 200, rate_bps: 5_000_000 },
  { label: 'Heavy congestion (8×)', bulk_rate_hz: 200, rate_bps: 1_000_000 },
  { label: 'No congestion', bulk_rate_hz: 2, rate_bps: 100_000_000 },
]

export function TrafficLab() {
  const [workload, setWorkload] = useState(DEFAULTS)
  const [report, setReport] = useState<TrafficComparison | null>(null)

  const schedulers = useAsync(() => trafficApi.schedulers(), [])
  const [compare, state] = useAction(trafficApi.compare)

  const set = <K extends keyof typeof DEFAULTS>(k: K, v: number) =>
    setWorkload((w) => ({ ...w, [k]: v }))

  const offered = workload.critical_rate_hz * workload.critical_size_bits +
    workload.bulk_rate_hz * workload.bulk_size_bits
  const load = offered / workload.rate_bps

  const run = async () => {
    const result = await compare(
      {
        critical_rate_hz: workload.critical_rate_hz,
        critical_size_bits: workload.critical_size_bits,
        bulk_rate_hz: workload.bulk_rate_hz,
        bulk_size_bits: workload.bulk_size_bits,
        duration_ms: workload.duration_ms,
        max_queue: workload.max_queue,
        seed: workload.seed,
      },
      {
        rate_bps: workload.rate_bps,
        propagation_ms: workload.propagation_ms,
        max_queue: workload.max_queue,
      },
    )
    if (result) setReport(result)
  }

  return (
    <>
      <Card
        title="Workload"
        hint="Two traffic classes share one constrained link. Offered load must exceed link capacity for congestion — and therefore for any difference between the schedulers — to exist."
        right={
          <Pill tone={load > 1 ? 'warn' : 'idle'}>
            {load.toFixed(2)}× capacity
          </Pill>
        }
      >
        {state.error && <Message kind="error">{state.error}</Message>}

        <div className="grid cols-2">
          <div>
            <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-faint)', margin: '2px 0 8px' }}>
              Critical — latency-sensitive
            </div>
            <Slider
              label="Rate"
              unit="pkt/s"
              value={workload.critical_rate_hz}
              min={0} max={500} step={5}
              onChange={(v) => set('critical_rate_hz', v)}
            />
            <Slider
              label="Packet size"
              unit="bits"
              value={workload.critical_size_bits}
              min={512} max={16000} step={64}
              onChange={(v) => set('critical_size_bits', v)}
            />
          </div>

          <div>
            <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-faint)', margin: '2px 0 8px' }}>
              Bulk — throughput-oriented
            </div>
            <Slider
              label="Rate"
              unit="pkt/s"
              value={workload.bulk_rate_hz}
              min={0} max={2000} step={10}
              onChange={(v) => set('bulk_rate_hz', v)}
            />
            <Slider
              label="Packet size"
              unit="bits"
              value={workload.bulk_size_bits}
              min={512} max={400000} step={512}
              onChange={(v) => set('bulk_size_bits', v)}
            />
          </div>
        </div>

        <Divider label="Link" />

        <div className="grid cols-3">
          <Slider label="Capacity" unit="Mbps" value={workload.rate_bps / 1e6} min={0.1} max={20} step={0.1} display={(v) => v.toFixed(1)} onChange={(v) => set('rate_bps', v * 1e6)} />
          <Slider label="Propagation" unit="ms" value={workload.propagation_ms} min={0} max={50} step={0.5} onChange={(v) => set('propagation_ms', v)} />
          <Slider label="Duration" unit="ms" value={workload.duration_ms} min={50} max={5000} step={50} onChange={(v) => set('duration_ms', v)} />
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 14, flexWrap: 'wrap' }}>
          <button className="btn" onClick={run} disabled={state.pending}>
            {state.pending ? <span className="spinner" /> : 'Run both schedulers'}
          </button>
          {PRESETS.map((p) => (
            <button
              key={p.label}
              className="btn secondary"
              style={{ padding: '5px 11px', fontSize: 12 }}
              onClick={() => setWorkload((w) => ({ ...w, bulk_rate_hz: p.bulk_rate_hz, rate_bps: p.rate_bps }))}
            >
              {p.label}
            </button>
          ))}
        </div>

        <div className="note">
          Offered {fmtBps(offered)} onto a {fmtBps(workload.rate_bps)} link.{' '}
          {load > 1
            ? 'The link is oversubscribed, so queueing delay — and a visible difference between schedulers — is expected.'
            : 'The link is not oversubscribed, so there is no queue to prioritise out of and both schedulers should behave alike.'}
        </div>
      </Card>

      {schedulers.data && (
        <div className="grid cols-2">
          {schedulers.data.schedulers.map((s) => (
            <Card key={s.id} title={s.label} hint={s.description} />
          ))}
        </div>
      )}

      {report ? <Results report={report} /> : (
        <Card title="Results">
          <Empty
            icon="⇅"
            title="No comparison run yet"
            hint="Configure a workload above and run both schedulers. Both see identical arrivals, so any difference is attributable to scheduling alone."
          />
        </Card>
      )}

      {report && (
        <div className="note warn">
          <span className="sim-badge">Queueing model</span>{' '}
          {report.caveat} Priority scheduling is best-effort and non-preemptive: it
          offers no hard real-time guarantee, and it does not make bulk traffic
          faster. For real network measurements use the Benchmark Lab.
        </div>
      )}
    </>
  )
}

function Results({ report }: { report: TrafficComparison }) {
  const by = Object.fromEntries(report.results.map((r) => [r.scheduler, r]))
  const fifo = by.fifo
  const prio = by.priority
  if (!fifo || !prio) return null

  const crit = (s: typeof fifo) => s.classes.critical
  const bulk = (s: typeof fifo) => s.classes.bulk

  const critGain =
    crit(fifo).latency_median_ms && crit(prio).latency_median_ms
      ? (1 - (crit(prio).latency_median_ms! / crit(fifo).latency_median_ms!)) * 100
      : null
  const bulkChange =
    bulk(fifo).throughput_bps > 0
      ? ((bulk(prio).throughput_bps - bulk(fifo).throughput_bps) / bulk(fifo).throughput_bps) * 100
      : null

  return (
    <>
      <div className="grid cols-2">
        <Card
          title="Critical traffic latency"
          hint="Median and 95th percentile queueing delay for the latency-sensitive class."
          right={
            <Pill tone={critGain === null ? 'idle' : critGain > 0 ? 'ok' : 'warn'}>
              {critGain === null
                ? 'no data'
                : `${critGain >= 0 ? '−' : '+'}${Math.abs(critGain).toFixed(0)}% with priority`}
            </Pill>
          }
        >
          <BarChart
            unit=" ms"
            log
            data={[
              { label: 'FIFO — median', value: crit(fifo).latency_median_ms, color: 'var(--idle)' },
              { label: 'Priority — median', value: crit(prio).latency_median_ms, color: 'var(--ok)' },
              { label: 'FIFO — p95', value: crit(fifo).latency_p95_ms, color: 'var(--idle)' },
              { label: 'Priority — p95', value: crit(prio).latency_p95_ms, color: 'var(--ok)' },
            ]}
          />
        </Card>

        <Card
          title="Bulk throughput"
          hint="What the throughput-oriented class actually achieved. This is the cost side of the trade-off."
          right={
            <Pill tone={bulkChange !== null && bulkChange < 0 ? 'warn' : 'ok'}>
              {bulkChange === null ? 'no data' : `${bulkChange >= 0 ? '+' : '−'}${Math.abs(bulkChange).toFixed(0)}% with priority`}
            </Pill>
          }
        >
          <BarChart
            unit=""
            data={[
              { label: 'FIFO', value: bulk(fifo).throughput_bps, color: 'var(--idle)' },
              { label: 'Priority', value: bulk(prio).throughput_bps, color: 'var(--warn)' },
            ]}
          />
          <div className="note">
            Prioritising critical traffic is not free. Bulk throughput is reported
            here alongside critical latency precisely so the trade-off is visible
            rather than implied.
          </div>
        </Card>
      </div>

      <Card title="Side by side" hint={report.table.split('\n')[0]}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Scheduler</th><th>Class</th><th>Delivered</th><th>Dropped</th>
                <th>Latency p50</th><th>Latency p95</th><th>Throughput</th>
              </tr>
            </thead>
            <tbody>
              {report.results.flatMap((r) =>
                (['critical', 'bulk'] as const).map((cls) => {
                  const m = r.classes[cls]
                  if (!m) return null
                  return (
                    <tr key={`${r.scheduler}-${cls}`}>
                      <td style={{ color: r.scheduler === 'priority' ? 'var(--ok)' : 'var(--text-dim)' }}>
                        {r.scheduler}
                      </td>
                      <td>{cls}</td>
                      <td>{m.packets_delivered} / {m.packets_offered}</td>
                      <td>{m.packets_dropped > 0 ? <span style={{ color: 'var(--bad)' }}>{m.packets_dropped}</span> : '0'}</td>
                      <td>{fmtMs(m.latency_median_ms)}</td>
                      <td>{fmtMs(m.latency_p95_ms)}</td>
                      <td>{fmtBps(m.throughput_bps)}</td>
                    </tr>
                  )
                }),
              )}
            </tbody>
          </table>
        </div>
        <Divider label="Run configuration" />
        <div className="grid cols-4">
          <Stat label="Offered load" value={fmtBps(report.offered_bps)} sub={`${(report.offered_bps / report.link_capacity_bps).toFixed(2)}× capacity`} />
          <Stat label="Link capacity" value={fmtBps(report.link_capacity_bps)} />
          <Stat label="Duration" value={`${report.workload.duration_ms} ms`} />
          <Stat label="Seed" value={report.workload.seed} sub="both schedulers, same arrivals" />
        </div>
      </Card>

      <Card title="What this run showed" hint="Computed from the measurements above, not written in advance.">
        <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.75, color: 'var(--text-dim)', fontSize: 13 }}>
          {report.findings.map((f, i) => <li key={i}>{f}</li>)}
        </ul>
        <Divider label="Limitations" />
        <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.75, color: 'var(--text-faint)', fontSize: 12.5 }}>
          {report.limitations.map((l, i) => <li key={i}>{l}</li>)}
        </ul>
      </Card>
    </>
  )
}

function Slider({
  label,
  unit,
  value,
  min,
  max,
  step,
  onChange,
  display,
}: {
  label: string
  unit: string
  value: number
  min: number
  max: number
  step: number
  onChange: (v: number) => void
  display?: (v: number) => string
}) {
  return (
    <label className="field">
      <span>
        {label} —{' '}
        <span style={{ color: 'var(--text)', fontFamily: 'var(--mono)' }}>
          {display ? display(value) : value.toLocaleString()} {unit}
        </span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  )
}
