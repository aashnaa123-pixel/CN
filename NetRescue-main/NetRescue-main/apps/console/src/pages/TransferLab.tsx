/**
 * Transfer Lab — real file upload, real multipath scheduling, real SHA-256.
 *
 * The progress bar is driven by the backend's own chunk counts. A transfer is
 * only shown as successful when the receiver hash matches the source hash; a
 * mismatch is shown as a failure with both hashes visible, never smoothed over.
 */
import { useMemo, useRef, useState } from 'react'
import { fileToBase64, transferApi, type PathInput } from '../api'
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
  fmtBytes,
  fmtDuration,
  fmtPct,
} from '../components'
import type { TransferResult } from '../types'

interface PathDraft extends PathInput {
  enabled: boolean
}

const DEFAULT_PATHS: PathDraft[] = [
  { path_id: 'path-1', fd_estimate_ms: 10, tp_estimate_bps: 1_000_000, loss_rate: 0.0, enabled: true },
  { path_id: 'path-2', fd_estimate_ms: 45, tp_estimate_bps: 8_000_000, loss_rate: 0.02, enabled: true },
  { path_id: 'path-3', fd_estimate_ms: 120, tp_estimate_bps: 500_000, loss_rate: 0.08, enabled: false },
]

export function TransferLab() {
  const [file, setFile] = useState<File | null>(null)
  const [paths, setPaths] = useState<PathDraft[]>(DEFAULT_PATHS)
  const [variant, setVariant] = useState('DB-DSP')
  const [chunkSize, setChunkSize] = useState(4096)
  const [maxRetries, setMaxRetries] = useState(3)
  const [result, setResult] = useState<TransferResult | null>(null)
  const [resultNote, setResultNote] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const variants = useAsync(() => transferApi.variants(), [])
  const [run, runState] = useAction(transferApi.run)

  const activePaths = useMemo(() => paths.filter((p) => p.enabled), [paths])
  const estimatedChunks = file && chunkSize > 0 ? Math.ceil(file.size / chunkSize) : 0
  const totalBytes = activePaths.reduce((a, p) => a + p.tp_estimate_bps, 0)

  const doRun = async () => {
    setResult(null)
    setResultNote(null)
    if (!file) {
      setResultNote('Choose a file before running a transfer.')
      return
    }
    try {
      const b64 = await fileToBase64(file)
      const outcome = await run({
        run_id: `transfer-${Date.now().toString(36)}`,
        data_b64: b64,
        scheduler_variant: variant,
        chunk_size_bytes: chunkSize,
        max_retries: maxRetries,
        paths: activePaths.map(({ enabled, ...p }) => p),
      })
      if (outcome) {
        setResult(outcome)
        if (!outcome.persisted && outcome.persistence_error) {
          setResultNote(outcome.persistence_error)
        }
      }
    } catch (err) {
      setResultNote(err instanceof Error ? err.message : String(err))
    }
  }

  const integrity =
    result?.integrity_ok === true ? 'ok' : result?.integrity_ok === false ? 'bad' : 'idle'

  return (
    <>
      <div className="grid cols-2">
        <Card
          title="File"
          hint="The real bytes are read in the browser and sent to the backend, which chunks, schedules and reassembles them. No fixture is used."
        >
          {runState.error && <Message kind="error">{runState.error}</Message>}
          {resultNote && <Message kind="warn">{resultNote}</Message>}

          <label className="field">
            <span>Choose a file</span>
            <input
              ref={fileInput}
              type="file"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null)
                setResult(null)
              }}
            />
          </label>

          {file ? (
            <div className="grid cols-3" style={{ marginBottom: 14 }}>
              <Stat label="File" value={file.name.length > 18 ? file.name.slice(0, 17) + '…' : file.name} sub={fmtBytes(file.size)} />
              <Stat label="Chunks" value={estimatedChunks.toLocaleString()} sub={`${fmtBytes(chunkSize)} each`} />
              <Stat label="Path capacity" value={fmtBps(totalBytes)} sub={`${activePaths.length} path${activePaths.length === 1 ? '' : 's'}`} />
            </div>
          ) : (
            <Empty icon="⬒" title="No file selected" hint="Pick a file above. It is sent to the backend and chunked there." />
          )}

          <div className="row">
            <label className="field">
              <span>Scheduler</span>
              <select value={variant} onChange={(e) => setVariant(e.target.value)}>
                {(variants.data?.scheduler_variants ?? [{ id: 'DB-DSP', label: 'DB-DSP' }]).map((s) => (
                  <option key={s.id} value={s.id}>{s.label}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Chunk size (bytes)</span>
              <input
                type="number"
                min={64}
                max={1048576}
                step={64}
                value={chunkSize}
                onChange={(e) => setChunkSize(Math.max(64, Number(e.target.value) || 4096))}
              />
            </label>
            <label className="field">
              <span>Max retries</span>
              <input
                type="number"
                min={0}
                max={20}
                value={maxRetries}
                onChange={(e) => setMaxRetries(Math.max(0, Math.min(20, Number(e.target.value) || 0)))}
              />
            </label>
          </div>

          <button
            className="btn"
            style={{ width: '100%' }}
            disabled={!file || runState.pending || activePaths.length === 0}
            onClick={doRun}
          >
            {runState.pending ? <><span className="spinner" /> Transferring…</> : 'Run transfer'}
          </button>
          {activePaths.length === 0 && (
            <div className="note warn">Enable at least one path below.</div>
          )}
        </Card>

        <Card
          title="Paths"
          hint="Distinct logical overlay paths. Enable at least two to see multipath scheduling, or one to compare against the single-path baseline."
        >
          {paths.map((p, i) => (
            <div
              key={p.path_id}
              style={{
                border: '1px solid var(--border)',
                borderRadius: 6,
                padding: 11,
                marginBottom: 9,
                opacity: p.enabled ? 1 : 0.5,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 8 }}>
                <input
                  type="checkbox"
                  checked={p.enabled}
                  onChange={(e) =>
                    setPaths((prev) =>
                      prev.map((x, j) => (j === i ? { ...x, enabled: e.target.checked } : x)),
                    )
                  }
                  aria-label={`Enable ${p.path_id}`}
                />
                <span style={{ fontFamily: 'var(--mono)', fontSize: 12.5, fontWeight: 600 }}>
                  {p.path_id}
                </span>
                <span className="spacer" style={{ flex: 1 }} />
                <span style={{ fontSize: 11, color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>
                  {fmtBps(p.tp_estimate_bps)}
                </span>
              </div>
              <div className="row">
                <label className="field" style={{ marginBottom: 0 }}>
                  <span>Forward delay (ms)</span>
                  <input
                    type="number" min={0} max={1000} step={1}
                    value={p.fd_estimate_ms}
                    onChange={(e) => setPaths((prev) => prev.map((x, j) => (j === i ? { ...x, fd_estimate_ms: Number(e.target.value) } : x)))}
                  />
                </label>
                <label className="field" style={{ marginBottom: 0 }}>
                  <span>Capacity (bps)</span>
                  <input
                    type="number" min={1} step={100000}
                    value={p.tp_estimate_bps}
                    onChange={(e) => setPaths((prev) => prev.map((x, j) => (j === i ? { ...x, tp_estimate_bps: Math.max(1, Number(e.target.value)) } : x)))}
                  />
                </label>
                <label className="field" style={{ marginBottom: 0 }}>
                  <span>Loss</span>
                  <input
                    type="number" min={0} max={1} step={0.01}
                    value={p.loss_rate}
                    onChange={(e) => setPaths((prev) => prev.map((x, j) => (j === i ? { ...x, loss_rate: Math.min(1, Math.max(0, Number(e.target.value))) } : x)))}
                  />
                </label>
              </div>
            </div>
          ))}
          <button
            className="btn secondary"
            style={{ width: '100%' }}
            disabled={paths.length >= 8}
            onClick={() =>
              setPaths((prev) => [
                ...prev,
                {
                  path_id: `path-${prev.length + 1}`,
                  fd_estimate_ms: 20,
                  tp_estimate_bps: 1_000_000,
                  loss_rate: 0,
                  enabled: false,
                },
              ])
            }
          >
            Add path
          </button>
        </Card>
      </div>

      {result ? <TransferResultView result={result} integrity={integrity} /> : (
        <Card title="Result">
          <Empty
            icon="⇄"
            title="No transfer run yet"
            hint="Choose a file, enable two or more paths, and run it. The integrity verdict comes from comparing the source and receiver SHA-256 hashes."
          />
        </Card>
      )}
    </>
  )
}

function TransferResultView({
  result,
  integrity,
}: {
  result: TransferResult
  integrity: 'ok' | 'bad' | 'idle'
}) {
  const progress =
    result.total_chunks > 0
      ? (result.delivered_chunks / result.total_chunks) * 100
      : 0
  const hashesMatch =
    result.source_sha256 !== null &&
    result.receiver_sha256 !== null &&
    result.source_sha256 === result.receiver_sha256

  return (
    <>
      <Card
        title="Result"
        right={
          <Pill tone={integrity}>
            {integrity === 'ok' ? 'verified' : integrity === 'bad' ? 'integrity failed' : 'no verdict'}
          </Pill>
        }
      >
        <div
          className="msg"
          style={{
            borderColor: integrity === 'ok' ? 'var(--ok)' : 'var(--bad)',
            background: integrity === 'ok' ? 'color-mix(in srgb, var(--ok) 12%, transparent)' : 'color-mix(in srgb, var(--bad) 13%, transparent)',
            color: 'var(--text)',
            flexDirection: 'column',
            gap: 4,
          }}
          role="status"
        >
          <div style={{ fontWeight: 600 }}>
            {integrity === 'ok'
              ? 'Transfer complete and verified'
              : integrity === 'bad'
                ? 'Transfer finished but the data does not match'
                : 'Transfer did not produce an integrity verdict'}
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
            {integrity === 'ok'
              ? `${result.delivered_chunks} of ${result.total_chunks} chunks reassembled. The receiver's SHA-256 matches the source exactly.`
              : integrity === 'bad'
                ? 'The reassembled bytes hash differently from the source. The transfer is reported as failed; it is not shown as a success.'
                : 'The receiver never produced a hash, so nothing can be claimed about whether the file arrived intact.'}
          </div>
        </div>

        <div style={{ margin: '16px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 5, color: 'var(--text-dim)' }}>
            <span>{result.delivered_chunks} / {result.total_chunks} chunks delivered</span>
            <span style={{ fontFamily: 'var(--mono)' }}>{fmtPct(progress)}</span>
          </div>
          <div style={{ height: 9, background: 'var(--bg-input)', borderRadius: 5, overflow: 'hidden' }}>
            <div
              style={{
                width: `${progress}%`,
                height: '100%',
                background: integrity === 'ok' ? 'var(--ok)' : 'var(--bad)',
                transition: 'width 0.25s',
              }}
            />
          </div>
        </div>

        <div className="grid cols-4">
          <Stat label="Status" value={result.status} tone={integrity === 'ok' ? 'ok' : 'bad'} />
          <Stat label="Elapsed" value={fmtDuration(result.duration_s)} />
          <Stat label="Goodput" value={fmtBps(result.goodput_bps)} />
          <Stat label="Retries" value={result.retransmission_count} sub={result.failed_chunks > 0 ? `${result.failed_chunks} chunks failed` : 'no chunk failures'} tone={result.retransmission_count > 0 ? 'warn' : undefined} />
        </div>

        <Divider label="Integrity" />
        <div className="grid cols-2">
          <div>
            <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-faint)', marginBottom: 4 }}>Source SHA-256</div>
            <div className="hash">{result.source_sha256 ?? '—'}</div>
          </div>
          <div>
            <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-faint)', marginBottom: 4 }}>Receiver SHA-256</div>
            <div className="hash" style={{ color: hashesMatch ? 'var(--ok)' : 'var(--bad)' }}>
              {result.receiver_sha256 ?? '— not produced'}
            </div>
          </div>
        </div>

        {!result.persisted && (
          <div className="note warn">
            This result was measured but not stored. {result.persistence_error}
          </div>
        )}
      </Card>

      {result.per_path.length > 0 && (
        <Card title="Per-path utilisation" hint="How the scheduler actually spread the file across the configured paths.">
          <BarChart
            unit=" chunks"
            data={result.per_path.map((p) => ({
              label: p.path_id,
              value: p.chunks_sent,
            }))}
          />
          <div className="table-wrap" style={{ marginTop: 14 }}>
            <table>
              <thead>
                <tr><th>Path</th><th>Chunks sent</th><th>Share</th><th>Retransmissions</th></tr>
              </thead>
              <tbody>
                {result.per_path.map((p) => (
                  <tr key={p.path_id}>
                    <td>{p.path_id}</td>
                    <td>{p.chunks_sent}</td>
                    <td>{fmtPct(p.share_pct)}</td>
                    <td>{p.retransmissions > 0 ? <span style={{ color: 'var(--warn)' }}>{p.retransmissions}</span> : '0'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="note">
            Out-of-order arrivals: {result.out_of_order_count === null ? 'not recorded for this transfer' : result.out_of_order_count}.
            {' '}Scheduler: <code className="kbd">{result.scheduler_variant}</code>.
            {' '}Transfer id: <code className="kbd">{result.transfer_id.slice(0, 13)}</code>.
          </div>
        </Card>
      )}
    </>
  )
}
