/**
 * Overview — the landing screen.
 *
 * Shows the live topology when one is running and says so plainly when it is
 * not, rather than rendering an empty diagram that looks like a healthy network
 * with no nodes.
 */
import { Link } from 'react-router-dom'
import { labApi, systemApi } from '../api'
import { useAsync } from '../hooks'
import {
  Card,
  Empty,
  EventLog,
  Loading,
  Message,
  Pill,
  Skeleton,
  Stat,
  fmtDuration,
} from '../components'
import { Topology } from '../Topology'
import type { EventsResult } from '../hooks'

export function Overview({ events }: { events: EventsResult }) {
  const topo = useAsync(() => labApi.topology(), [], 4000)
  const health = useAsync(() => systemApi.health(), [], 10000)
  const ready = useAsync(() => systemApi.ready(), [], 15000)

  const t = topo.data
  const running = t?.running ?? false
  const failedNodes = t?.nodes.filter((n) => n.failed).length ?? 0
  const downLinks = t?.links.filter((l) => l.status === 'DOWN').length ?? 0

  const total = t?.nodes.reduce(
    (acc, n) => ({
      sent: acc.sent + n.counters.sent,
      received: acc.received + n.counters.received,
      forwarded: acc.forwarded + n.counters.forwarded,
      dropped: acc.dropped + n.counters.dropped_no_route + n.counters.dropped_invalid,
    }),
    { sent: 0, received: 0, forwarded: 0, dropped: 0 },
  )

  const dbOk = ready.data?.status === 'ready'

  return (
    <>
      {topo.error && <Message kind="error">{topo.error}</Message>}

      <Card
        title="System"
        right={
          <Pill tone={health.data?.status === 'ok' ? 'ok' : 'bad'}>
            {health.data?.status === 'ok' ? 'API online' : 'API unreachable'}
          </Pill>
        }
      >
        <div className="grid cols-4">
          <Stat label="API" value={health.data ? 'up' : '—'} sub={health.data?.version} tone={health.data ? 'ok' : 'bad'} />
          <Stat
            label="PostgreSQL"
            value={ready.error ? 'down' : ready.data ? 'ready' : '—'}
            sub={ready.error ? 'history unavailable' : dbOk ? 'persistence on' : 'checking'}
            tone={ready.error ? 'warn' : dbOk ? 'ok' : 'idle'}
          />
          <Stat label="Event stream" value={events.connected ? 'live' : 'offline'} sub={`${events.events.length} buffered`} tone={events.connected ? 'ok' : 'warn'} />
          <Stat label="Topology" value={running ? `${t?.nodes.length ?? 0} nodes` : 'none'} sub={running ? `${t?.layout} layout` : 'not running'} tone={running ? 'ok' : 'idle'} />
        </div>
        {ready.error && (
          <div className="note warn">
            PostgreSQL is not reachable, so experiment history and run details are
            unavailable. Benchmarks, the Recovery Lab and the Traffic Lab still work.
          </div>
        )}
      </Card>

      <Card
        title="Live topology"
        hint={running ? `Uptime ${fmtDuration(t?.uptime_s)}` : 'No topology is running.'}
        right={
          running ? (
            <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
              {failedNodes > 0 && <Pill tone="bad">{failedNodes} node{failedNodes > 1 ? 's' : ''} failed</Pill>}
              {downLinks > 0 && <Pill tone="warn">{downLinks} link{downLinks > 1 ? 's' : ''} down</Pill>}
              {failedNodes === 0 && downLinks === 0 && <Pill tone="ok">all healthy</Pill>}
            </div>
          ) : undefined
        }
      >
        {topo.initial && topo.loading ? (
          <Loading label="Reading topology" />
        ) : !running ? (
          <Empty
            icon="⌗"
            title="No topology is running"
            hint="Start a topology in the Recovery Lab to bring up real forwarding nodes on loopback sockets. Nothing is shown here because no nodes exist — this is not a healthy network with no traffic."
            action={<Link className="btn" to="/recovery">Open the Recovery Lab</Link>}
          />
        ) : (
          <>
            <Topology nodes={t!.nodes} links={t!.links} />
            <div className="grid cols-4" style={{ marginTop: 18 }}>
              <Stat label="Messages sent" value={total!.sent} />
              <Stat label="Messages received" value={total!.received} />
              <Stat label="Forwarded" value={total!.forwarded} sub="relayed by a third node" />
              <Stat label="Dropped" value={total!.dropped} sub="no route or rejected" tone={total!.dropped > 0 ? 'warn' : undefined} />
            </div>
            {t!.failures.length > 0 && (
              <div className="note warn">
                {t!.failures.length} injected failure{t!.failures.length > 1 ? 's' : ''} active:{' '}
                {t!.failures.map((f) => `${f.kind} ${f.target}`).join(', ')}.
              </div>
            )}
          </>
        )}
      </Card>

      <div className="grid cols-2">
        <Card title="Nodes" hint="Live per-node counters from the forwarding processes.">
          {!running ? (
            <Empty icon="◌" title="No nodes" hint="Start a topology to populate this table." />
          ) : topo.loading && !t ? (
            <Skeleton rows={3} />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Node</th><th>State</th><th>Sent</th><th>Recv</th><th>Fwd</th><th>Dropped</th>
                  </tr>
                </thead>
                <tbody>
                  {t!.nodes.map((n) => (
                    <tr key={n.node_id}>
                      <td>{n.node_id}</td>
                      <td>
                        <Pill tone={n.failed ? 'bad' : n.running ? 'ok' : 'idle'}>
                          {n.failed ? 'failed' : n.running ? 'up' : 'stopped'}
                        </Pill>
                      </td>
                      <td>{n.counters.sent}</td>
                      <td>{n.counters.received}</td>
                      <td>{n.counters.forwarded}</td>
                      <td>{n.counters.dropped_no_route + n.counters.dropped_invalid}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card
          title="Live events"
          hint="Pushed over WebSocket as the topology changes."
          right={events.events.length ? <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>{events.events.length}</span> : undefined}
        >
          <EventLog
            events={events.events}
            limit={40}
            emptyHint="Start a topology or inject a failure in the Recovery Lab. Events appear here the moment the backend emits them."
          />
        </Card>
      </div>
    </>
  )
}
