/**
 * Recovery Lab — start a real topology, break it, and watch it route around the
 * failure.
 *
 * The screen never claims recovery. "Delivered" appears only when the backend
 * reports the destination acknowledged the message; when a destination is gone,
 * the result is shown as not delivered with the reason, and the route table
 * shows UNREACHABLE rather than hiding the gap.
 */
import { useState } from 'react'
import { labApi } from '../api'
import { useAction, useAsync } from '../hooks'
import {
  Card,
  Divider,
  Empty,
  EventLog,
  Loading,
  Message,
  Pill,
  Stat,
  linkTone,
} from '../components'
import { Topology } from '../Topology'
import type { DeliveryResult, LinkState, RouteEntry } from '../types'
import type { EventsResult } from '../hooks'

const LAYOUTS = [
  { id: 'line', label: 'Line', hint: 'A→B→C. Killing a middle node cuts the path — nothing can route around it.' },
  { id: 'ring', label: 'Ring', hint: 'A→B→C→D→A. Killing one node leaves the rest reachable the long way round.' },
  { id: 'full', label: 'Full mesh', hint: 'Every node reaches every other directly. Most resilient, least interesting to break.' },
] as const

const SUGGESTIONS = ['A,B,C,D']

export function RecoveryLab({ events }: { events: EventsResult }) {
  const [nodeText, setNodeText] = useState('A,B,C,D')
  const [layout, setLayout] = useState<string>('ring')
  const [source, setSource] = useState('A')
  const [destination, setDestination] = useState('C')
  const [lastDelivery, setLastDelivery] = useState<DeliveryResult | null>(null)

  const topo = useAsync(() => labApi.topology(), [], 3000)
  // The routing table only changes when something happens, and `running` never
  // flips during a fault — so it needs its own refresh trigger. Bumping `routeTick`
  // after every action is what keeps this table from showing pre-failure routes
  // while the diagram beside it shows a dead node.
  const [routeTick, setRouteTick] = useState(0)
  const routes = useAsync(
    () => (topo.data?.running ? labApi.routes() : Promise.resolve(null)),
    [topo.data?.running, routeTick],
  )
  const history = useAsync(
    () => (topo.data?.running ? labApi.history() : Promise.resolve(null)),
    [topo.data?.running, routeTick],
  )

  const [start, startState] = useAction(labApi.startTopology)
  const [fail, failState] = useAction(labApi.fail)
  const [clear, clearState] = useAction(labApi.clearFailures)
  const [send, sendState] = useAction(labApi.deliver)

  const t = topo.data
  const running = t?.running ?? false
  const nodeIds = t?.nodes.map((n) => n.node_id) ?? []
  const selectedLayout = LAYOUTS.find((l) => l.id === layout)!

  const doDeliver = async (from: string, to: string) => {
    const result = await send(from, to)
    // Only overwrite the previous result once the backend has answered.
    if (result) setLastDelivery(result)
    topo.reload()
    setRouteTick((t) => t + 1)
  }

  /** Re-read both the diagram and the routing table after an action. */
  const refreshAll = () => {
    topo.reload()
    setRouteTick((t) => t + 1)
  }

  return (
    <>
      {/* ---- Topology control ---- */}
      <Card
        title="Topology"
        hint="Starts real forwarding nodes, each an independent listener on a loopback port with its own forwarding table and heartbeat detector."
      >
        {startState.error && <Message kind="error">{startState.error}</Message>}
        {failState.error && <Message kind="error">{failState.error}</Message>}
        {clearState.error && <Message kind="error">{clearState.error}</Message>}

        {!running ? (
          <>
            <div className="row">
              <label className="field">
                <span>Nodes</span>
                <input
                  value={nodeText}
                  onChange={(e) => setNodeText(e.target.value)}
                  placeholder="A,B,C,D"
                  aria-describedby="node-help"
                />
              </label>
              <label className="field">
                <span>Layout</span>
                <select value={layout} onChange={(e) => setLayout(e.target.value)}>
                  {LAYOUTS.map((l) => (
                    <option key={l.id} value={l.id}>{l.label}</option>
                  ))}
                </select>
              </label>
              <div className="shrink" style={{ paddingBottom: 12 }}>
                <button
                  className="btn"
                  disabled={startState.pending}
                  onClick={() =>
                    start(nodeText.split(',').map((s) => s.trim()).filter(Boolean), layout)
                  }
                >
                  {startState.pending ? <span className="spinner" /> : `Start ${selectedLayout.label.toLowerCase()}`}
                </button>
              </div>
            </div>
            <div className="row" style={{ marginTop: -4 }}>
              <span className="sub" id="node-help" style={{ fontSize: 12, color: 'var(--text-faint)' }}>
                {selectedLayout.hint}
              </span>
            </div>
            <div style={{ marginTop: 10, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {SUGGESTIONS.map((s) => (
                <button key={s} className="btn secondary" style={{ padding: '3px 9px', fontSize: 12 }} onClick={() => setNodeText(s)}>
                  {s}
                </button>
              ))}
            </div>
          </>
        ) : (
          <>
            <Topology nodes={t!.nodes} links={t!.links} highlightPath={lastDelivery?.path} />
            <div className="grid cols-4" style={{ marginTop: 16 }}>
              <Stat label="Layout" value={t!.layout} />
              <Stat label="Uptime" value={`${Math.round(t!.uptime_s ?? 0)}s`} />
              <Stat
                label="Injected faults"
                value={t!.failures.length}
                tone={t!.failures.length ? 'bad' : 'ok'}
              />
              <Stat
                label="Delivered"
                value={history.data?.deliveries.filter((d) => d.delivered).length ?? 0}
                sub={`of ${history.data?.deliveries.length ?? 0} attempts`}
              />
            </div>
            <div style={{ marginTop: 14, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button
                className="btn danger"
                disabled={clearState.pending || t!.failures.length === 0}
                onClick={() => clear().then(() => refreshAll())}
              >
                Clear all faults
              </button>
              <button
                className="btn secondary"
                disabled={startState.pending}
                onClick={() =>
                  start(nodeText.split(',').map((s) => s.trim()).filter(Boolean), layout)
                }
              >
                Restart topology
              </button>
            </div>
          </>
        )}
      </Card>

      {/* ---- Controls and delivery ---- */}
      <div className="grid cols-2">
        <Card
          title="Inject a failure"
          hint={
            running
              ? 'A node fault closes that node’s real listener. A link fault marks that link down in every node’s router. Either way the topology is not pretending — the packets genuinely cannot get through.'
              : 'Start a topology first.'
          }
        >
          {!running ? (
            <Empty icon="⚡" title="No topology running" hint="Start one above to enable fault injection." />
          ) : (
            <>
              {t!.failures.length > 0 && (
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-faint)', marginBottom: 6 }}>
                    Active faults
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {t!.failures.map((f) => (
                      <Pill key={`${f.kind}-${f.target}`} tone="bad">{f.kind} {f.target}</Pill>
                    ))}
                  </div>
                </div>
              )}
              <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-faint)', marginBottom: 6 }}>
                Kill a node
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 16 }}>
                {nodeIds.map((id) => {
                  const isDown = t!.failures.some((f) => f.kind === 'node' && f.target === id)
                  return (
                    <button
                      key={id}
                      className="btn danger"
                      style={{ padding: '5px 12px' }}
                      disabled={failState.pending || isDown}
                      onClick={() => fail('node', id).then(() => refreshAll())}
                    >
                      {isDown ? `${id} down` : `Kill ${id}`}
                    </button>
                  )
                })}
              </div>

              <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-faint)', marginBottom: 6 }}>
                Cut a link
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {t!.links.map((l) => {
                  const isDown = l.status === 'DOWN'
                  return (
                    <button
                      key={l.link_id}
                      className="btn secondary"
                      style={{ padding: '5px 12px' }}
                      disabled={failState.pending || isDown}
                      onClick={() => fail('link', l.link_id).then(() => refreshAll())}
                    >
                      {isDown ? `${l.link_id} down` : `Cut ${l.link_id}`}
                    </button>
                  )
                })}
              </div>
            </>
          )}
        </Card>

        <Card
          title="Send and verify"
          hint="A message is only reported delivered when the destination node itself acknowledges. A successful write to the first hop is not delivery."
        >
          {!running ? (
            <Empty icon="⇄" title="No topology running" hint="Start one above to send traffic." />
          ) : (
            <>
              {sendState.error && <Message kind="error">{sendState.error}</Message>}
              <div className="row">
                <label className="field">
                  <span>From</span>
                  <select value={source} onChange={(e) => setSource(e.target.value)}>
                    {nodeIds.map((id) => <option key={id} value={id}>{id}</option>)}
                  </select>
                </label>
                <label className="field">
                  <span>To</span>
                  <select value={destination} onChange={(e) => setDestination(e.target.value)}>
                    {nodeIds.map((id) => <option key={id} value={id}>{id}</option>)}
                  </select>
                </label>
                <div className="shrink" style={{ paddingBottom: 12 }}>
                  <button
                    className="btn"
                    disabled={sendState.pending || nodeIds.length < 2}
                    onClick={() => doDeliver(source, destination)}
                  >
                    {sendState.pending ? <span className="spinner" /> : 'Send'}
                  </button>
                </div>
              </div>

              {lastDelivery && (
                <div
                  className={`msg ${lastDelivery.delivered ? 'ok' : 'error'}`}
                  style={{ marginBottom: 0 }}
                  role="status"
                >
                  <span>{lastDelivery.delivered ? '✓' : '✕'}</span>
                  <span>
                    <strong>
                      {lastDelivery.delivered ? 'Delivered' : 'Not delivered'}
                    </strong>{' '}
                    {lastDelivery.source} → {lastDelivery.destination}
                    {lastDelivery.delivered && lastDelivery.path && (
                      <> via <code className="kbd">{lastDelivery.path.join(' → ')}</code></>
                    )}
                    {!lastDelivery.delivered && lastDelivery.reason && (
                      <> — {lastDelivery.reason}</>
                    )}
                  </span>
                </div>
              )}

              <div style={{ marginTop: 14, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {nodeIds.filter((id) => id !== source).map((id) => (
                  <button
                    key={id}
                    className="btn secondary"
                    style={{ padding: '4px 10px', fontSize: 12 }}
                    disabled={sendState.pending}
                    onClick={() => doDeliver(source, id)}
                  >
                    {source} → {id}
                  </button>
                ))}
              </div>

              {history.data && history.data.deliveries.length > 0 && (
                <>
                  <Divider label="Recent deliveries" />
                  <div className="table-wrap" style={{ maxHeight: 190, overflowY: 'auto' }}>
                    <table>
                      <thead>
                        <tr><th>Route</th><th>Result</th><th>Path</th></tr>
                      </thead>
                      <tbody>
                        {history.data.deliveries.slice(0, 12).map((d, i) => (
                          <tr key={i}>
                            <td>{d.source} → {d.destination}</td>
                            <td>
                              <Pill tone={d.delivered ? 'ok' : 'bad'}>
                                {d.delivered ? 'delivered' : 'failed'}
                              </Pill>
                            </td>
                            <td className="text" style={{ color: 'var(--text-dim)' }}>
                              {d.path?.join(' → ') ?? d.reason ?? '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </>
          )}
        </Card>
      </div>

      {/* ---- Routes and events ---- */}
      <div className="grid cols-2">
        <Card
          title="Routing table"
          hint="Each node’s view. UNREACHABLE means exactly that — there is no path, and the lab does not pretend otherwise."
        >
          {!running ? (
            <Empty icon="⌸" title="No topology running" />
          ) : routes.initial && routes.loading ? (
            <Loading label="Reading routing tables" />
          ) : routes.error ? (
            <Message kind="error">{routes.error}</Message>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>From → To</th><th>Status</th><th>Path</th></tr>
                </thead>
                <tbody>
                  {Object.entries(routes.data?.routes ?? {}).flatMap(([from, dests]) =>
                    Object.entries(dests).map(([to, r]) => (
                      <RouteRow key={`${from}-${to}`} from={from} to={to} route={r} />
                    )),
                  )}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card
          title="Event timeline"
          hint="Live from the WebSocket. Includes events that fired before this page opened."
        >
          <EventLog events={events.events} limit={50} />
        </Card>
      </div>

      <Card title="Link state">
        {!running ? (
          <Empty icon="◌" title="No topology running" />
        ) : (
          <div className="grid cols-3">
            {t!.links.map((l) => (
              <div key={l.link_id} style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                <span className="mono" style={{ fontFamily: 'var(--mono)', minWidth: 62 }}>{l.link_id}</span>
                <Pill tone={l.status === 'DOWN' ? 'bad' : linkTone(l.state_a as LinkState)}>
                  {l.status === 'DOWN' ? 'down' : l.state_a.toLowerCase()}
                </Pill>
                <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
                  {l.state_b.toLowerCase()} at {l.node_b_id}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  )
}

function RouteRow({ from, to, route }: { from: string; to: string; route: RouteEntry }) {
  return (
    <tr>
      <td>{from} → {to}</td>
      <td>
        <Pill tone={route.status === 'OK' ? 'ok' : 'bad'}>
          {route.status === 'OK' ? 'ok' : 'unreachable'}
        </Pill>
      </td>
      <td className="text" style={{ color: 'var(--text-dim)' }} title={route.reason}>
        {route.status === 'OK' ? route.path?.join(' → ') : route.reason ?? '—'}
      </td>
    </tr>
  )
}
