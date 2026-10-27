/**
 * Security & Audit — authorised actions, denials and host capability.
 *
 * The audit trail is read-only here by design: this screen reports what
 * protection exists, it does not offer a way to edit or suppress the record.
 */
import { useState } from 'react'
import { systemApi } from '../api'
import { useAsync } from '../hooks'
import {
  Card,
  Empty,
  Message,
  Pill,
  Stat,
  Unavailable,
} from '../components'

export function SecurityAudit() {
  const [filter, setFilter] = useState<'ALL' | 'ALLOW' | 'DENY'>('ALL')
  const [limit, setLimit] = useState(100)

  const security = useAsync(() => systemApi.security(), [])
  const audit = useAsync(
    () => systemApi.audit(filter === 'ALL' ? limit : limit),
    [filter, limit],
  )

  const tls = (security.data?.tls ?? {}) as Record<string, unknown>
  const emulation = (security.data?.emulation ?? {}) as Record<string, unknown>
  const entries = audit.data?.entries ?? []
  const denials = entries.filter((e) => e.result === 'DENY').length
  const failures = audit.data?.persistence?.persist_failures ?? 0
  const tlsReady = tls.configured === true || tls.available === true

  return (
    <>
      <Card title="Authorisation" hint="How the control plane decides who may do what.">
        <div className="grid cols-3">
          <Stat label="Read" value="readonly" sub="any caller, including anonymous" tone="ok" />
          <Stat label="Write" value="operator" sub="required for every control action" tone="warn" />
          <Stat label="Emulation" value="operator" sub="required to apply tc/netem" tone="warn" />
        </div>
        <div className="note">
          The prototype identifies the caller by the <code className="kbd">X-NetRescue-Principal</code>{' '}
          and <code className="kbd">X-NetRescue-Role</code> headers, because it binds to
          loopback only. A request with no role header defaults to{' '}
          <code className="kbd">readonly</code>, so an unlabelled request is safe by
          default rather than privileged. Every write is recorded in the audit trail
          below, including the ones that were refused.
        </div>
      </Card>

      <div className="grid cols-2">
        <Card title="Transport security" hint="mTLS material for node-to-node and operator traffic.">
          {security.error ? (
            <Message kind="error">{security.error}</Message>
          ) : !tlsReady ? (
            <Unavailable
              what="Mutual TLS"
              why={
                <>
                  No certificate material is present. The prototype sends traffic in
                  cleartext over loopback, which is acceptable only because it never
                  leaves the host.
                </>
              }
              fix={<code className="kbd">mkdir certs && openssl req -x509 ... -keyout certs/ca.key -out certs/ca.crt</code>}
            />
          ) : (
            <div>
              <Pill tone="ok">configured</Pill>
              <pre className="mono" style={{ marginTop: 10 }}>{JSON.stringify(tls, null, 2)}</pre>
            </div>
          )}
        </Card>

        <Card title="Host capability" hint="What this machine can actually do, reported rather than assumed.">
          {security.error ? (
            <Message kind="error">{security.error}</Message>
          ) : (
            <div>
              <Pill tone={emulation.supported ? 'ok' : 'warn'}>
                {emulation.supported ? 'emulation available' : 'emulation unavailable'}
              </Pill>
              <pre className="mono" style={{ marginTop: 10 }}>{JSON.stringify(emulation, null, 2)}</pre>
            </div>
          )}
        </Card>
      </div>

      <Card
        title="Audit trail"
        hint="Written at the point of the action. Detail fields are scrubbed of anything secret before they are stored, so no credential can appear here."
        right={
          <div style={{ display: 'flex', gap: 7, alignItems: 'center' }}>
            <select
              style={{ width: 'auto' }}
              value={filter}
              onChange={(e) => setFilter(e.target.value as typeof filter)}
              aria-label="Filter by result"
            >
              <option value="ALL">All results</option>
              <option value="ALLOW">Allowed only</option>
              <option value="DENY">Denied only</option>
            </select>
            <select
              style={{ width: 'auto' }}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              aria-label="Number of entries"
            >
              {[25, 50, 100, 250].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
        }
      >
        {audit.error && (
          <Message kind="warn">
            {audit.error} The audit trail is persisted to PostgreSQL, so it is
            unavailable while the database is down. In-memory entries are still
            being written and will appear once it returns.
          </Message>
        )}

        {!audit.error && entries.length > 0 && (
          <div className="grid cols-3" style={{ marginBottom: 14 }}>
            <Stat label="Entries" value={entries.length} />
            <Stat label="Allowed" value={entries.filter((e) => e.result === 'ALLOW').length} tone="ok" />
            <Stat label="Denied" value={denials} tone={denials > 0 ? 'bad' : 'ok'} />
          </div>
        )}

        {audit.data && (
          <div className={`note${failures > 0 ? ' warn' : ''}`}>
            {failures > 0 ? (
              <>
                <strong>{failures} audit record{failures > 1 ? 's' : ''} could not be written to
                PostgreSQL.</strong>{' '}
                They are still held in memory for this process, but they are lost on
                restart. An empty list here may mean "nothing happened" or "nothing
                could be recorded"; this line tells you which.
              </>
            ) : (
              <>Every record below was written to PostgreSQL. An empty table therefore means no
              operator actions have been recorded, not that recording has failed.</>
            )}
          </div>
        )}

        {!audit.error && entries.length === 0 ? (
          <Empty
            icon="⛨"
            title="No audit entries"
            hint="Entries are written when an operator performs a control action — starting a topology, injecting a failure, running a transfer."
          />
        ) : (
          <div className="table-wrap" style={{ maxHeight: 460, overflowY: 'auto' }}>
            <table>
              <thead>
                <tr><th>When</th><th>Principal</th><th>Action</th><th>Resource</th><th>Result</th></tr>
              </thead>
              <tbody>
                {entries
                  .filter((e) => filter === 'ALL' || e.result === filter)
                  .map((e, i) => (
                    <tr key={i}>
                      <td>{new Date(e.timestamp).toLocaleString()}</td>
                      <td>{e.principal}</td>
                      <td>{e.action}</td>
                      <td className="text" style={{ color: 'var(--text-dim)' }}>{e.resource}</td>
                      <td><Pill tone={e.result === 'ALLOW' ? 'ok' : 'bad'}>{e.result}</Pill></td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}
