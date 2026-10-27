/**
 * Benchmark Lab — real measurements over loopback sockets.
 *
 * The runner opens actual UDP or TCP sockets, so the numbers are host
 * loopback figures, not internet figures. That distinction is stated on the
 * screen rather than left for the reader to infer.
 */
import { useState } from 'react'
import { benchmarkApi, systemApi } from '../api'
import { useAction, useAsync } from '../hooks'
import {
  BarChart,
  Card,
  Divider,
  Empty,
  Message,
  Pill,
  Stat,
  Unavailable,
  fmtBps,
  fmtBytes,
  fmtDuration,
  fmtMs,
  fmtPct,
} from '../components'

interface BenchResult {
  run_id: string
  persisted?: boolean
  persistence_error?: string
  label?: string
  protocol?: string
  message_count?: number
  message_size_bytes?: number
  received_count?: number
  lost_count?: number
  /** Fraction 0..1. `loss_rate_pct` is the display form. */
  loss_rate?: number
  loss_rate_pct?: string
  run_duration_s?: number
  throughput_bps?: number
  rtt_median_ms?: number | null
  rtt_p95_ms?: number | null
  jitter_ms?: number | null
  rtt_samples?: number
  echo_arrivals?: number
  environment?: Record<string, unknown>
  measurement_semantics?: Record<string, string>
  [k: string]: unknown
}

const PRESETS = [
  { label: 'UDP · 256 B · 200 msgs', message_count: 200, message_size_bytes: 256, protocol: 'UDP' },
  { label: 'UDP · 1400 B · 200 msgs', message_count: 200, message_size_bytes: 1400, protocol: 'UDP' },
  { label: 'TCP · 1400 B · 200 msgs', message_count: 200, message_size_bytes: 1400, protocol: 'TCP' },
  { label: 'UDP · 1200 B · 2000 msgs', message_count: 2000, message_size_bytes: 1200, protocol: 'UDP' },
]

export function BenchmarkLab() {
  const [protocol, setProtocol] = useState('UDP')
  const [messageCount, setMessageCount] = useState(200)
  const [messageSize, setMessageSize] = useState(256)
  const [interval, setIntervalMs] = useState(1)
  const [result, setResult] = useState<BenchResult | null>(null)
  const [csv, setCsv] = useState<{ filename: string; content: string } | null>(null)

  const security = useAsync(() => systemApi.security(), [])
  const [run, runState] = useAction(benchmarkApi.run)
  const [exportCsv, exportState] = useAction(systemApi.exportCsv)

  const emulation = (security.data?.emulation ?? {}) as Record<string, unknown>
  const netemSupported = emulation.supported === true || emulation.linux === true
  const envReport = emulation as { supported?: boolean; reason?: string; platform?: string }

  const doRun = async () => {
    setCsv(null)
    const outcome = (await run({
      protocol,
      target_host: '127.0.0.1',
      target_port: 9101,
      message_count: messageCount,
      message_size_bytes: messageSize,
      send_interval_s: interval / 1000,
    })) as BenchResult | undefined
    if (outcome) setResult(outcome)
  }

  const doExport = async () => {
    if (!result) return
    const outcome = await exportCsv(result.run_id)
    if (outcome) setCsv({ filename: outcome.filename, content: outcome.content })
  }

  return (
    <>
      <Card
        title="Scenario"
        hint="The runner opens real UDP or TCP sockets on loopback. These are host-local measurements — they characterise this machine's loopback path, not an internet or a real LAN."
        right={<Pill tone="info">loopback</Pill>}
      >
        {runState.error && <Message kind="error">{runState.error}</Message>}
        {exportState.error && <Message kind="error">{exportState.error}</Message>}

        <div className="row">
          <label className="field">
            <span>Protocol</span>
            <select value={protocol} onChange={(e) => setProtocol(e.target.value)}>
              <option value="UDP">UDP</option>
              <option value="TCP">TCP</option>
            </select>
          </label>
          <label className="field">
            <span>Messages</span>
            <input
              type="number" min={1} max={100000}
              value={messageCount}
              onChange={(e) => setMessageCount(Math.max(1, Number(e.target.value) || 1))}
            />
          </label>
          <label className="field">
            <span>Size (bytes)</span>
            <input
              type="number" min={8} max={65536} step={8}
              value={messageSize}
              onChange={(e) => setMessageSize(Math.max(8, Number(e.target.value) || 8))}
            />
          </label>
          <label className="field">
            <span>Interval (ms)</span>
            <input
              type="number" min={0} max={10000} step={0.1}
              value={interval}
              onChange={(e) => setIntervalMs(Math.max(0, Number(e.target.value) || 0))}
            />
          </label>
          <div className="shrink" style={{ paddingBottom: 12 }}>
            <button className="btn" onClick={doRun} disabled={runState.pending}>
              {runState.pending ? <><span className="spinner" /> Running…</> : 'Run benchmark'}
            </button>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {PRESETS.map((p) => (
            <button
              key={p.label}
              className="btn secondary"
              style={{ padding: '4px 11px', fontSize: 12 }}
              onClick={() => {
                setProtocol(p.protocol)
                setMessageCount(p.message_count)
                setMessageSize(p.message_size_bytes)
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      </Card>

      {result ? (
        <>
          <Card
            title="Measurements"
            right={
              <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                <Pill tone={result.protocol === 'UDP' ? 'info' : 'ok'}>{result.protocol}</Pill>
                <Pill tone={result.persisted ? 'ok' : 'warn'}>
                  {result.persisted ? 'persisted' : 'not persisted'}
                </Pill>
              </div>
            }
          >
            {result.persistence_error && <Message kind="warn">{result.persistence_error}</Message>}

            <div className="grid cols-4">
              <Stat label="RTT p50" value={fmtMs(result.rtt_median_ms)} sub={`${result.rtt_samples ?? 0} samples`} />
              <Stat label="Jitter" value={fmtMs(result.jitter_ms)} sub={`RTT p95 ${fmtMs(result.rtt_p95_ms)}`} />
              <Stat
                label="Loss"
                value={result.loss_rate_pct ?? fmtPct((result.loss_rate ?? 0) * 100)}
                sub={`${result.lost_count ?? 0} of ${result.message_count ?? 0} lost`}
                tone={(result.lost_count ?? 0) > 0 ? 'warn' : 'ok'}
              />
              <Stat label="Throughput" value={fmtBps(result.throughput_bps)} sub={fmtDuration(result.run_duration_s)} />
            </div>

            <Divider label="Delivery detail" />
            <BarChart
              data={[
                { label: 'Sent', value: result.message_count ?? null, color: 'var(--accent)' },
                { label: 'Received', value: result.received_count ?? null, color: 'var(--ok)' },
                { label: 'Lost', value: result.lost_count ?? null, color: 'var(--bad)' },
                { label: 'Echo replies', value: result.echo_arrivals ?? null, color: 'var(--warn)' },
              ]}
            />

            {result.measurement_semantics && (
              <div className="note">
                {Object.entries(result.measurement_semantics).map(([k, v]) => (
                  <div key={k}><strong>{k}:</strong> {v}</div>
                ))}
              </div>
            )}

            <div style={{ marginTop: 16, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button
                className="btn secondary"
                onClick={doExport}
                disabled={!result.persisted || exportState.pending}
              >
                {exportState.pending ? <span className="spinner" /> : 'Export CSV'}
              </button>
              <span style={{ fontSize: 11.5, color: 'var(--text-faint)', alignSelf: 'center' }}>
                run <code className="kbd">{result.run_id}</code> · payload {fmtBytes(messageSize)} × {messageCount}
              </span>
            </div>
            {!result.persisted && (
              <div className="note warn">
                CSV export needs the run to be in the database. Start PostgreSQL and
                re-run to make this result exportable.
              </div>
            )}

            {csv && (
              <>
                <Divider label={csv.filename} />
                <pre className="mono" style={{ maxHeight: 240, overflow: 'auto' }}>{csv.content}</pre>
                <button
                  className="btn secondary"
                  style={{ marginTop: 10 }}
                  onClick={() => {
                    const blob = new Blob([csv.content], { type: 'text/csv' })
                    const url = URL.createObjectURL(blob)
                    const a = document.createElement('a')
                    a.href = url
                    a.download = csv.filename
                    a.click()
                    URL.revokeObjectURL(url)
                  }}
                >
                  Download {csv.filename}
                </button>
              </>
            )}
          </Card>

          {result.environment && Object.keys(result.environment).length > 0 && (
            <Card title="Environment" hint="Recorded with the run so results stay interpretable.">
              <pre className="mono">{JSON.stringify(result.environment, null, 2)}</pre>
            </Card>
          )}
        </>
      ) : (
        <Card title="Measurements">
          <Empty
            icon="📈"
            title="No benchmark run yet"
            hint="Configure a scenario and run it. Every number below comes from the runner's own measurements."
          />
        </Card>
      )}

      <Card
        title="Kernel network emulation (tc / netem)"
        hint="Latency, jitter, loss and rate limiting applied with tc/netem on Linux."
      >
        {!netemSupported ? (
          <Unavailable
            what="tc / netem emulation"
            why={
              <>
                This host is {envReport.platform ?? 'not Linux'}
                {envReport.reason ? `, and ${envReport.reason}` : ', so kernel traffic control is not available here.'}
                {' '}The benchmark runner above is unaffected — it measures real sockets without needing tc.
              </>
            }
            fix={
              <code className="kbd">
                wsl --install -d Ubuntu && sudo apt install iproute2
              </code>            }
          />
        ) : (
          <div>
            <Pill tone="ok">available</Pill>
            <pre className="mono" style={{ marginTop: 10 }}>
              {JSON.stringify(emulation, null, 2)}
            </pre>
          </div>
        )}
        <div className="note">
          Emulated conditions are applied by the kernel and verified after being
          set. When application fails, the failure is recorded and reported — a
          simulated substitute is never presented as a real measurement.
        </div>
      </Card>
    </>
  )
}
