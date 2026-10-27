/**
 * TopologyCanvas.tsx — the workbench surface.
 *
 * An SVG graph rather than a canvas, and hand-built rather than pulled from a
 * library, for one reason: everything drawn here has to be a statement about
 * what the backend actually did. A rendering library would be a dependency to
 * keep current for a graph of at most twelve nodes, and the one thing this
 * screen must not do is imply a certainty it does not have. Each colour and
 * each dashed line is tied to a field the API returned — `status`, `in_route`,
 * `failed` — and the packet animation is a reading of `last_delivery.path`,
 * never a decoration placed over a route that was not used.
 *
 * The only thing animated here that is not a backend result is the motion of the
 * packet between hops, and it is drawn along exactly the path the runtime
 * reported.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { CanvasLink, CanvasNode } from './types';

export const NODE_RADIUS = 30;

export interface Selection {
    kind: 'node' | 'link' | null;
    id: string | null;
}

export interface PacketRun {
    path: string[];
    /** Changing this restarts the animation, so a second identical route still animates. */
    key: number;
    delivered: boolean;
}

interface Props {
    nodes: CanvasNode[];
    links: CanvasLink[];
    route: string[] | null;
    previousRoute: string[] | null;
    source: string | null;
    destination: string | null;
    selection: Selection;
    connectingFrom: string | null;
    packet: PacketRun | null;
    onSelectNode: (id: string) => void;
    onSelectLink: (id: string) => void;
    onSelectEmpty: () => void;
    onMoveNode: (id: string, x: number, y: number) => void;
    onConnectStart: (id: string) => void;
    onConnectComplete: (from: string, to: string) => void;
    onConnectCancel: () => void;
}

const VIEW_W = 1000;
const VIEW_H = 440;

const ROLE_LABEL: Record<string, string> = {
    source: 'SOURCE',
    router: 'ROUTER',
    destination: 'DEST',
};

function linkId(a: string, b: string) {
    return `${a < b ? a : b}-${a < b ? b : a}`;
}

export default function TopologyCanvas(props: Props) {
    const {
        nodes, links, route, previousRoute, source, destination,
        selection, connectingFrom, packet,
        onSelectNode, onSelectLink, onSelectEmpty,
        onMoveNode, onConnectStart, onConnectComplete, onConnectCancel,
    } = props;

    const svgRef = useRef<SVGSVGElement | null>(null);
    const [drag, setDrag] = useState<{ id: string; dx: number; dy: number } | null>(null);
    const [pending, setPending] = useState<{ from: string; x: number; y: number } | null>(null);

    const byId = new Map(nodes.map(n => [n.node_id, n]));

    const toSvg = useCallback((clientX: number, clientY: number) => {
        const rect = svgRef.current?.getBoundingClientRect();
        if (!rect) return { x: 0, y: 0 };
        return {
            x: ((clientX - rect.left) / rect.width) * VIEW_W,
            y: ((clientY - rect.top) / rect.height) * VIEW_H,
        };
    }, []);

    // -- dragging a node -------------------------------------------------
    useEffect(() => {
        if (!drag) return;

        const move = (event: PointerEvent) => {
            const { x, y } = toSvg(event.clientX, event.clientY);
            onMoveNode(drag.id, x - drag.dx, y - drag.dy);
        };
        const up = () => setDrag(null);

        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
        return () => {
            window.removeEventListener('pointermove', move);
            window.removeEventListener('pointerup', up);
        };
    }, [drag, onMoveNode, toSvg]);

    // -- drawing a connection -------------------------------------------
    useEffect(() => {
        if (!connectingFrom) return;

        const move = (event: PointerEvent) => {
            const { x, y } = toSvg(event.clientX, event.clientY);
            setPending({ from: connectingFrom, x, y });
        };
        const key = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                setPending(null);
                onConnectCancel();
            }
        };
        window.addEventListener('pointermove', move);
        window.addEventListener('keydown', key);
        return () => {
            window.removeEventListener('pointermove', move);
            window.removeEventListener('keydown', key);
        };
    }, [connectingFrom, onConnectCancel, toSvg]);

    const routeSet = new Set(routeLinks(route));
    const previousSet = new Set(routeLinks(previousRoute));

    const handleNodeDown = (event: React.PointerEvent, node: CanvasNode) => {
        event.stopPropagation();
        const { x, y } = toSvg(event.clientX, event.clientY);
        if (connectingFrom) {
            if (connectingFrom !== node.node_id) {
                onConnectComplete(connectingFrom, node.node_id);
                setPending(null);
                onConnectCancel();
            }
            return;
        }
        onSelectNode(node.node_id);
        setDrag({ id: node.node_id, dx: x - node.x, dy: y - node.y });
    };

    return (
        <div className="relative w-full h-full">
            <svg
                ref={svgRef}
                viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
                preserveAspectRatio="xMidYMid meet"
                className="w-full h-full touch-none select-none"
                style={{ background: 'radial-gradient(ellipse at 30% 0%, #1a0508 0%, #000 62%)' }}
                onPointerDown={event => {
                    if (event.target === svgRef.current) {
                        setPending(null);
                        onSelectEmpty();
                        onConnectCancel();
                    }
                }}
            >
                <defs>
                    <pattern id="nr-grid" width="40" height="40" patternUnits="userSpaceOnUse">
                        <path d="M 40 0 L 0 0 0 40" fill="none" stroke="rgba(255,255,255,0.035)" strokeWidth="1" />
                    </pattern>
                    <filter id="nr-glow" x="-50%" y="-50%" width="200%" height="200%">
                        <feGaussianBlur stdDeviation="4" result="blur" />
                        <feMerge>
                            <feMergeNode in="blur" />
                            <feMergeNode in="SourceGraphic" />
                        </feMerge>
                    </filter>
                </defs>

                <rect width={VIEW_W} height={VIEW_H} fill="url(#nr-grid)" />

                {/* Links */}
                {links.map(link => (
                    <LinkShape
                        key={link.link_id}
                        link={link}
                        a={byId.get(link.node_a)}
                        b={byId.get(link.node_b)}
                        onRoute={routeSet.has(link.link_id)}
                        onPrevious={previousSet.has(link.link_id)}
                        selected={selection.kind === 'link' && selection.id === link.link_id}
                        onSelect={() => onSelectLink(link.link_id)}
                    />
                ))}

                {/* The connection being drawn */}
                {pending && byId.get(pending.from) && (
                    <line
                        x1={byId.get(pending.from)!.x}
                        y1={byId.get(pending.from)!.y}
                        x2={pending.x}
                        y2={pending.y}
                        stroke="#crimson"
                        strokeWidth="1.5"
                        strokeDasharray="5 4"
                        opacity="0.85"
                    />
                )}

                <PacketAnimation packet={packet} byId={byId} />

                {/* Nodes */}
                {nodes.map(node => (
                    <NodeShape
                        key={node.node_id}
                        node={node}
                        isSource={node.node_id === source}
                        isDestination={node.node_id === destination}
                        onRoute={route ? route.includes(node.node_id) : false}
                        onPrevious={previousRoute ? previousRoute.includes(node.node_id) : false}
                        selected={selection.kind === 'node' && selection.id === node.node_id}
                        connecting={connectingFrom === node.node_id}
                        target={connectingFrom !== null && connectingFrom !== node.node_id}
                        onPointerDown={event => handleNodeDown(event, node)}
                        onConnectStart={onConnectStart}
                    />
                ))}

                {nodes.length === 0 && (
                    <text
                        x={VIEW_W / 2} y={VIEW_H / 2}
                        textAnchor="middle"
                        fill="rgba(255,255,255,0.30)"
                        fontSize="15"
                        style={{ fontFamily: '"Space Mono", monospace' }}
                    >
                        Empty workspace — add a source and a destination, or load the example.
                    </text>
                )}
            </svg>

            {/* Legend — every entry is a state the backend actually reports. */}
            <div className="absolute bottom-2 left-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-white/60 pointer-events-none">
                <Legend swatch="#2dd4bf" label="healthy" />
                <Legend swatch="#f5b54a" label="suspect" />
                <Legend swatch="#ef4444" label="failed" />
                <Legend swatch="#ffffff" label="active route" />
                <Legend swatch="rgba(255,255,255,0.35)" label="previous route" dashed />
            </div>
        </div>
    );
}

function Legend({ swatch, label, dashed }: { swatch: string; label: string; dashed?: boolean }) {
    return (
        <span className="flex items-center gap-1.5">
            <span
                className="inline-block w-4"
                style={{
                    borderTop: `2px ${dashed ? 'dashed' : 'solid'} ${swatch}`,
                    height: 0,
                }}
            />
            {label}
        </span>
    );
}

function routeLinks(path: string[] | null): string[] {
    if (!path || path.length < 2) return [];
    const out: string[] = [];
    for (let i = 0; i < path.length - 1; i += 1) out.push(linkId(path[i], path[i + 1]));
    return out;
}

function LinkShape({
    link, a, b, onRoute, onPrevious, selected, onSelect,
}: {
    link: CanvasLink;
    a?: CanvasNode;
    b?: CanvasNode;
    onRoute: boolean;
    onPrevious: boolean;
    selected: boolean;
    onSelect: () => void;
}) {
    if (!a || !b) return null;

    // Trim to the node edge so the line meets the ring, not its centre.
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const length = Math.hypot(dx, dy) || 1;
    const ux = dx / length;
    const uy = dy / length;
    const x1 = a.x + ux * NODE_RADIUS;
    const y1 = a.y + uy * NODE_RADIUS;
    const x2 = b.x - ux * NODE_RADIUS;
    const y2 = b.y - uy * NODE_RADIUS;

    const failed = !link.enabled || link.status === 'DOWN';
    const suspect = !failed && (link.state_a === 'SUSPECT' || link.state_b === 'SUSPECT');

    let stroke = 'rgba(255,255,255,0.20)';
    let width = 1.5;
    let dash: string | undefined;
    let opacity = 1;

    if (failed) {
        stroke = '#ef4444';
        width = 2;
        dash = '7 5';
    } else if (onRoute) {
        stroke = '#ffffff';
        width = 3;
        opacity = 0.95;
    } else if (onPrevious) {
        stroke = 'rgba(255,255,255,0.40)';
        width = 1.5;
        dash = '3 6';
    } else if (suspect) {
        stroke = '#f5b54a';
        width = 2.5;
    } else if (!link.enabled) {
        stroke = 'rgba(255,255,255,0.10)';
    }

    if (selected) {
        stroke = '#crimson';
        width = Math.max(width, 3);
    }

    return (
        <g onPointerDown={event => { event.stopPropagation(); onSelect(); }} style={{ cursor: 'pointer' }}>
            {/* A wide invisible hit area — a 1.5px line is not clickable. */}
            <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="transparent" strokeWidth={16} />
            <line
                x1={x1} y1={y1} x2={x2} y2={y2}
                stroke={stroke}
                strokeWidth={width}
                strokeDasharray={dash}
                strokeLinecap="round"
                opacity={opacity}
                filter={onRoute ? 'url(#nr-glow)' : undefined}
            />
            {failed && (
                <text
                    x={(x1 + x2) / 2}
                    y={(y1 + y2) / 2 - 7}
                    textAnchor="middle"
                    fill="#ef4444"
                    fontSize="10"
                    style={{ fontFamily: '"Space Mono", monospace', pointerEvents: 'none' }}
                >
                    {link.link_id}
                </text>
            )}
        </g>
    );
}

function NodeShape({
    node, isSource, isDestination, onRoute, onPrevious, selected,
    connecting, target, onPointerDown, onConnectStart,
}: {
    node: CanvasNode;
    isSource: boolean;
    isDestination: boolean;
    onRoute: boolean;
    onPrevious: boolean;
    selected: boolean;
    connecting: boolean;
    target: boolean;
    onPointerDown: (event: React.PointerEvent) => void;
    onConnectStart: (id: string) => void;
}) {
    const failed = node.failed;
    const idle = !node.running && !failed;

    let stroke = '#2dd4bf';          // healthy — restrained teal
    if (failed) stroke = '#ef4444';
    else if (idle) stroke = 'rgba(255,255,255,0.28)';
    else if (node.state === 'UP' && node.detector) {
        const readings = Object.values(node.detector);
        if (readings.some(r => r.state === 'SUSPECT')) stroke = '#f5b54a';
    }
    if (selected) stroke = '#ffffff';
    if (onPrevious && !onRoute) stroke = 'rgba(255,255,255,0.40)';

    return (
        <g
            transform={`translate(${node.x} ${node.y})`}
            onPointerDown={onPointerDown}
            style={{ cursor: 'grab' }}
        >
            {onRoute && (
                <circle r={NODE_RADIUS + 8} fill="none" stroke="rgba(255,255,255,0.18)" strokeWidth="1" />
            )}
            <circle
                r={NODE_RADIUS}
                fill="rgba(0,0,0,0.85)"
                stroke={stroke}
                strokeWidth={selected ? 2.5 : 1.75}
                strokeDasharray={failed ? '5 4' : undefined}
                filter={onRoute ? 'url(#nr-glow)' : undefined}
                opacity={target ? 0.5 : 1}
            />

            <text
                textAnchor="middle"
                dy="-2"
                fill="#ffffff"
                fontSize="15"
                style={{ fontFamily: '"Space Mono", monospace', pointerEvents: 'none' }}
            >
                {node.node_id}
            </text>
            <text
                textAnchor="middle"
                dy="15"
                fill="rgba(255,255,255,0.42)"
                fontSize="8"
                letterSpacing="0.08em"
                style={{ fontFamily: '"Space Mono", monospace', pointerEvents: 'none' }}
            >
                {failed ? 'FAILED' : ROLE_LABEL[node.role] ?? node.role.toUpperCase()}
            </text>
            {isSource && (
                <text
                    textAnchor="middle"
                    dy={NODE_RADIUS + 16}
                    fill="#ffffff"
                    fontSize="9"
                    letterSpacing="0.1em"
                    style={{ fontFamily: '"Space Mono", monospace', pointerEvents: 'none' }}
                >
                    ▼ FROM
                </text>
            )}
            {isDestination && (
                <text
                    textAnchor="middle"
                    dy={NODE_RADIUS + 16}
                    fill="#ffffff"
                    fontSize="9"
                    letterSpacing="0.1em"
                    style={{ fontFamily: '"Space Mono", monospace', pointerEvents: 'none' }}
                >
                    ▼ TO
                </text>
            )}

            {/* The handle you pull a connection from. */}
            <circle
                cx={NODE_RADIUS}
                cy={0}
                r={6}
                fill={connecting ? '#ffffff' : 'rgba(255,255,255,0.16)'}
                stroke="rgba(255,255,255,0.5)"
                strokeWidth="1"
                style={{ cursor: 'crosshair' }}
                onPointerDown={event => {
                    event.stopPropagation();
                    onConnectStart(node.node_id);
                }}
            >
                <title>Drag to another node to connect</title>
            </circle>
        </g>
    );
}

/**
 * The packet.
 *
 * Walks the exact path the runtime reported and stops at the destination. When
 * the delivery failed there is no path to walk, so nothing is drawn — a packet
 * flying along a route the backend did not use would be the single most
 * misleading thing this screen could do.
 */
function PacketAnimation({
    packet, byId,
}: {
    packet: PacketRun | null;
    byId: Map<string, CanvasNode>;
}) {
    const [t, setT] = useState(0);
    const frame = useRef<number | null>(null);
    const started = useRef(0);

    const points = (packet?.path ?? [])
        .map(id => byId.get(id))
        .filter((n): n is CanvasNode => Boolean(n));

    const drawable = Boolean(packet?.delivered && points.length > 1);

    useEffect(() => {
        if (!drawable || !packet) {
            setT(0);
            return;
        }
        started.current = performance.now();
        const DURATION = Math.min(2200, 420 * (points.length - 1));
        const tick = (now: number) => {
            const progress = Math.min(1, (now - started.current) / DURATION);
            setT(progress);
            if (progress < 1) frame.current = requestAnimationFrame(tick);
        };
        frame.current = requestAnimationFrame(tick);
        return () => {
            if (frame.current !== null) cancelAnimationFrame(frame.current);
        };
        // points.length is derived from the packet path; the key restarts it.
    }, [packet?.key, drawable, points.length]);

    if (!drawable || !packet) return null;

    const segments = points.length - 1;
    const position = t * segments;
    const index = Math.min(segments - 1, Math.floor(position));
    const local = position - index;
    const from = points[index];
    const to = points[index + 1];
    if (!from || !to) return null;

    const x = from.x + (to.x - from.x) * local;
    const y = from.y + (to.y - from.y) * local;

    return (
        <g pointerEvents="none">
            <circle cx={x} cy={y} r={11} fill="rgba(255,255,255,0.14)" />
            <circle cx={x} cy={y} r={4.5} fill="#ffffff" filter="url(#nr-glow)" />
            {t >= 1 && (
                <circle
                    cx={to.x} cy={to.y} r={NODE_RADIUS + 6}
                    fill="none" stroke="#2dd4bf" strokeWidth="1.5"
                    opacity={0.7}
                />
            )}
        </g>
    );
}