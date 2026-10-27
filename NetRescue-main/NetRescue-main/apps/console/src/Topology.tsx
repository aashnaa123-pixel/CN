/**
 * Topology.tsx — the live topology diagram.
 *
 * Layout is computed from the node list, not stored, so it always matches what
 * the backend actually has. Every visual state is driven by a real field:
 * a node is drawn "failed" only when the backend says `failed: true`, and a
 * link is dashed only when its status is DOWN. Nothing animates on a timer.
 */
import { useMemo } from 'react'
import type { LabLink, LabNode } from './types'

interface Placed {
  node: LabNode
  x: number
  y: number
}

export function Topology({
  nodes,
  links,
  highlightPath,
  width = 760,
  height = 330,
}: {
  nodes: LabNode[]
  links: LabLink[]
  /** When set, this A-to-B path is drawn highlighted (the route last used). */
  highlightPath?: string[]
  width?: number
  height?: number
}) {
  const placed = useMemo(() => place(nodes, width, height), [nodes, width, height])
  const byId = useMemo(
    () => new Map(placed.map((p) => [p.node.node_id, p])),
    [placed],
  )

  // Highlight only edges the highlighted path actually traverses.
  const activeEdges = useMemo(() => {
    if (!highlightPath || highlightPath.length < 2) return new Set<string>()
    const set = new Set<string>()
    for (let i = 0; i < highlightPath.length - 1; i += 1) {
      const [a, b] = [highlightPath[i], highlightPath[i + 1]].sort()
      set.add(`${a}-${b}`)
    }
    return set
  }, [highlightPath])

  if (!nodes.length) return null

  return (
    <div className="topo-wrap">
      <svg className="topo" viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: 'auto', maxHeight: 380 }} role="img" aria-label="Network topology">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)" />
          </marker>
        </defs>

        {links.map((link) => {
          const a = byId.get(link.node_a_id)
          const b = byId.get(link.node_b_id)
          if (!a || !b) return null
          const key = [link.node_a_id, link.node_b_id].sort().join('-')
          const active = activeEdges.has(key)
          return (
            <line
              key={link.link_id}
              x1={a.x} y1={a.y} x2={b.x} y2={b.y}
              className={`topo-edge${link.status === 'DOWN' ? ' down' : ''}`}
              style={active ? { stroke: 'var(--accent)', strokeWidth: 2.5 } : undefined}
            >
              <title>{`${link.link_id}: ${link.status}`}</title>
            </line>
          )
        })}

        {placed.map(({ node, x, y }) => {
          const cls = node.failed ? 'failed' : node.running ? 'up' : 'down'
          return (
            <g key={node.node_id}>
              <circle className={`topo-node ${cls}`} cx={x} cy={y} r={25}>
                <title>
                  {`${node.node_id} — ${node.failed ? 'FAILED' : node.running ? 'running' : 'stopped'}\n` +
                    `port ${node.port}\n` +
                    `sent ${node.counters.sent}  received ${node.counters.received}\n` +
                    `forwarded ${node.counters.forwarded}  no-route ${node.counters.dropped_no_route}`}
                </title>
              </circle>
              <text className="topo-label" x={x} y={y + 4} textAnchor="middle">{node.node_id}</text>
              <text className="topo-sub" x={x} y={y + 41} textAnchor="middle">
                {node.failed ? 'FAILED' : `:${node.port}`}
              </text>
            </g>
          )
        })}
      </svg>

      <div className="topo-legend">
        <span><span className="dot" style={{ color: 'var(--ok)' }} /> running</span>
        <span><span className="dot" style={{ color: 'var(--bad)' }} /> failed</span>
        <span><span className="dot" style={{ color: 'var(--idle)' }} /> stopped</span>
        <span style={{ marginLeft: 'auto' }}>
          hover a node for its live counters
          {highlightPath?.length ? ` · highlighting path ${highlightPath.join(' → ')}` : ''}
        </span>
      </div>
    </div>
  )
}

/**
 * Arrange nodes on a circle, ordered so neighbours in the graph tend to sit
 * near each other. Deterministic: the same node set always produces the same
 * picture, so the diagram does not reshuffle between polls.
 */
function place(nodes: LabNode[], width: number, height: number): Placed[] {
  if (nodes.length === 0) return []
  if (nodes.length === 1) {
    return [{ node: nodes[0], x: width / 2, y: height / 2 }]
  }

  const ordered = orderByAdjacency(nodes)
  const cx = width / 2
  const cy = height / 2
  const radius = Math.min(width, height) / 2 - 56

  return ordered.map((node, i) => {
    const angle = (2 * Math.PI * i) / ordered.length - Math.PI / 2
    return {
      node,
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
    }
  })
}

/** Greedy nearest-neighbour ordering using the link list, falling back to id. */
function orderByAdjacency(nodes: LabNode[]): LabNode[] {
  const ids = nodes.map((n) => n.node_id).sort()
  const adjacency = new Map<string, Set<string>>()
  for (const id of ids) adjacency.set(id, new Set())
  for (const [a, links] of nodes.map((n) => [n.node_id, Object.keys(n.link_states)] as const)) {
    for (const b of links) adjacency.get(a)?.add(b)
  }

  const remaining = new Set(ids)
  const ordered: LabNode[] = []
  let current = ids[0]
  while (remaining.size) {
    const node = nodes.find((n) => n.node_id === current)
    if (node) ordered.push(node)
    remaining.delete(current)
    const neighbours = [...(adjacency.get(current) ?? [])]
      .filter((n) => remaining.has(n))
      .sort()
    current = neighbours[0] ?? [...remaining].sort()[0]
  }
  return ordered
}
