/**
 * Run Details — configuration, raw metrics and how to reproduce a run.
 *
 * Every value shown here comes from the stored run, so the page cannot drift
 * from what was actually executed.
 */
import { Link, useParams } from 'react-router-dom'
import { benchmarkApi, systemApi } from '../api'
import { useAsync } from '../hooks'
import {
  Card,
  Empty,
  Message,
  Pill,
  Stat,
  fmtBps,
  fmtMs,
  fmtPct,
} from '../components'

export function RunDetails() {
  const { runId = '' } = useParams()
  const metrics = useAsync(() => systemApi.runMetrics(runId), [runId], 10000)
  const bench = useAsync(() => benchmarkApi.get(runId), [runId])

  const b = (bench.data ?? {}) as Record<string, unknown>
  const num = (k: string) => (typeof b[k] === 'number' ? (b[k] as number) : null)

  return (
    <>
      <div style={{ marginBottom: 14 }}>
        <Link to="/history" className="btn secondary" style={{ padding: '5px 12px', fontSize: 12.5 }}>
          ← Back to history
        </Link>
      </div>

      <Card
        title={`Run ${runId}`}
        hint="Configuration and metrics as recorded by the backend at the time of the run."
        right={bench.data ? <Pill tone="ok">found</Pill> : undefined}
      >
        {bench.error && !bench.loading && <Message kind="error">{bench.error}</Message>}

        {bench.data && (
          <>
            <div className="grid cols-4">
              <Stat label="RTT p50" value={fmtMs(num('rtt_median_ms'))} />
              <Stat label="RTT p95" value={fmtMs(num('rtt_p95_ms'))} />
              <Stat label="Jitter" value={fmtMs(num('jitter_ms'))} />
              <Stat
                label="Loss"
                value={
                  typeof b.loss_rate_pct === 'string'
                    ? b.loss_rate_pct
                    : fmtPct((num('loss_rate') ?? 0) * 100)
                }
              />
            </div>
            <div style={{ marginTop: 14 }}>
              <Stat label="Throughput" value={fmtBps(num('throughput_bps'))} sub={num('run_duration_s') !== null ? `${num('run_duration_s')}s` : undefined} />
            </div>
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-faint)', marginBottom: 6 }}>
                Full run record
              </div>
              <pre className="mono" style={{ maxHeight: 380, overflow: 'auto' }}>
                {JSON.stringify(b, null, 2)}
              </pre>
            </div>
          </>
        )}
      </Card>

      <Card
        title="Recorded metrics"
        hint="Every sample the runner persisted for this run."
        right={
          metrics.data ? (
            <button
              className="btn secondary"
              style={{ padding: '4px 11px', fontSize: 12 }}
              onClick={async () => {
                const out = await systemApi.exportCsv(runId)
                if (!out) return
                const blob = new Blob([out.content], { type: 'text/csv' })
                const url = URL.createObjectURL(blob)
                const a = document.createElement('a')
                a.href = url
                a.download = out.filename
                a.click()
                URL.revokeObjectURL(url)
              }}
            >
              Export CSV
            </button>
          ) : undefined
        }
      >
        {metrics.error ? (
          <Message kind="warn">
            {metrics.error} Metrics and CSV export need PostgreSQL. Start it and
            this page will fill in.
          </Message>
        ) : metrics.data && metrics.data.count > 0 ? (
          <div className="table-wrap" style={{ maxHeight: 420, overflowY: 'auto' }}>
            <table>
              <thead><tr><th>Time</th><th>Metric</th><th>Value</th><th>Unit</th><th>Labels</th></tr></thead>
              <tbody>
                {metrics.data.samples.map((s, i) => (
                  <tr key={i}>
                    <td>{new Date(s.timestamp).toLocaleTimeString()}</td>
                    <td>{s.name}</td>
                    <td>{s.value}</td>
                    <td>{s.unit ?? '—'}</td>
                    <td className="text" style={{ color: 'var(--text-dim)' }}>
                      {Object.keys(s.labels ?? {}).length
                        ? Object.entries(s.labels).map(([k, v]) => `${k}=${String(v)}`).join(' ')
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : metrics.loading ? (
          <div className="skeleton" style={{ height: 70 }} />
        ) : (
          <Empty
            icon="▤"
            title="No metric samples for this run"
            hint="Either the run recorded none, or it was not persisted because PostgreSQL was unavailable when it executed."
          />
        )}
      </Card>

      <Card title="Reproduce this run" hint="Commands that recreate the same conditions.">
        <pre className="mono">
{`# 1. Start the API (creates the schema if needed)
uvicorn apps.api.main:app --host 127.0.0.1 --port 8000

# 2. Re-run the benchmark with the same configuration
curl -X POST http://127.0.0.1:8000/api/benchmarks \\
  -H 'Content-Type: application/json' \\
  -H 'X-NetRescue-Principal: operator' \\
  -H 'X-NetRescue-Role: operator' \\
  -d '{"run_id": "${runId}", "protocol": "UDP", "target_host": "127.0.0.1",
       "target_port": 9101, "message_count": 200, "message_size_bytes": 256,
       "send_interval_s": 0.001}'

# 3. Read the metrics back
curl http://127.0.0.1:8000/api/runs/${runId}/metrics

# 4. Export as CSV
curl http://127.0.0.1:8000/api/runs/${runId}/export.csv`}
        </pre>
        <div className="note">
          Fix the random seed in the request to get a byte-identical workload.
          Without it, run-to-run variation is expected and is why the comparison
          harness reports medians and p95 across repetitions rather than single runs.
        </div>
      </Card>
    </>
  )
}
