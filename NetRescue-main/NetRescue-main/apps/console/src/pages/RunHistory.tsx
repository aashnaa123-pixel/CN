/**
 * Run History — previously saved experiments.
 *
 * This screen needs PostgreSQL. When it is down it says so explicitly, because an
 * empty table would otherwise read as "you have never run anything".
 */
import { useState } from 'react'
import { systemApi, transferApi } from '../api'
import { useAsync } from '../hooks'
import {
  Card,
  Empty,
  Message,
  Pill,
  Stat,
  fmtBps,
  fmtDuration,
} from '../components'
import { Link } from 'react-router-dom'

export function RunHistory() {
  const [limit, setLimit] = useState(50)
  const transfers = useAsync(() => transferApi.list(), [limit])
  const audit = useAsync(() => systemApi.audit(20), [])

  const dbDown = transfers.error?.includes('PostgreSQL') || transfers.error?.includes('needs PostgreSQL')

  return (
    <>
      <Card
        title="Saved transfers"
        hint="Transfers are only retained when PostgreSQL is running. Each row carries the integrity verdict recorded at the time."
        right={
          <select
            style={{ width: 'auto' }}
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            aria-label="Number of transfers to load"
          >
            {[10, 25, 50, 100].map((n) => (
              <option key={n} value={n}>{n} rows</option>
            ))}
          </select>
        }
      >
        {transfers.error && (
          dbDown ? (
            <Message kind="warn">{transfers.error}</Message>
          ) : (
            <Message kind="error">{transfers.error}</Message>
          )
        )}

        {transfers.initial && transfers.loading ? (
          <div className="skeleton" style={{ height: 90 }} />
        ) : transfers.data && transfers.data.length > 0 ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Transfer</th><th>Scheduler</th><th>Chunks</th><th>Integrity</th>
                  <th>Goodput</th><th>Retries</th><th>Size</th>
                </tr>
              </thead>
              <tbody>
                {transfers.data.map((t) => (
                  <tr key={t.transfer_id}>
                    <td>{t.transfer_id.slice(0, 13)}</td>
                    <td>{t.scheduler_variant}</td>
                    <td>{t.delivered_chunks} / {t.total_chunks}</td>
                    <td>
                      <Pill tone={t.integrity_ok === true ? 'ok' : t.integrity_ok === false ? 'bad' : 'idle'}>
                        {t.integrity_ok === true ? 'verified' : t.integrity_ok === false ? 'failed' : 'unknown'}
                      </Pill>
                    </td>
                    <td>{fmtBps(t.goodput_bps)}</td>
                    <td>{t.retransmission_count}</td>
                    <td>{fmtDuration(t.duration_s)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : !transfers.error ? (
          <Empty
            icon="☰"
            title="No transfers recorded yet"
            hint="Run one in the Transfer Lab while PostgreSQL is up and it will appear here."
            action={<Link className="btn" to="/transfer">Open the Transfer Lab</Link>}
          />
        ) : null}
      </Card>

      {transfers.data && transfers.data.length > 0 && (
        <div className="grid cols-4">
          <Stat
            label="Transfers"
            value={transfers.data.length}
            sub={transfers.data.filter((t) => t.integrity_ok).length + ' verified'}
          />
          <Stat
            label="Chunks"
            value={transfers.data.reduce((a, t) => a + t.total_chunks, 0)}
          />
          <Stat
            label="Retries"
            value={transfers.data.reduce((a, t) => a + t.retransmission_count, 0)}
            tone={transfers.data.reduce((a, t) => a + t.retransmission_count, 0) > 0 ? 'warn' : undefined}
          />
          <Stat
            label="Failures"
            value={transfers.data.filter((t) => t.integrity_ok === false || t.failed_chunks > 0).length}
            tone={transfers.data.some((t) => t.integrity_ok === false) ? 'bad' : 'ok'}
          />
        </div>
      )}

      <Card
        title="Recent operator actions"
        hint="The authorised-action trail. Details are scrubbed of secrets at write time, so nothing sensitive can appear here."
        right={<Link className="btn secondary" style={{ padding: '4px 11px', fontSize: 12 }} to="/security">Full audit</Link>}
      >
        {audit.error ? (
          <Message kind="warn">{audit.error}</Message>
        ) : audit.data && audit.data.entries.length > 0 ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>When</th><th>Principal</th><th>Action</th><th>Result</th></tr></thead>
              <tbody>
                {audit.data.entries.slice(0, 12).map((e, i) => (
                  <tr key={i}>
                    <td>{new Date(e.timestamp).toLocaleString()}</td>
                    <td>{e.principal}</td>
                    <td>{e.action}</td>
                    <td>
                      <Pill tone={e.result === 'ALLOW' ? 'ok' : 'bad'}>{e.result}</Pill>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty icon="⛨" title="No audit entries yet" hint="Operator actions are recorded here as they happen." />
        )}
      </Card>
    </>
  )
}
