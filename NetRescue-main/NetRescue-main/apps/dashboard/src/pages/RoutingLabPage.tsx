/**
 * RoutingLabPage — Dijkstra shortest-path routing (SIMULATION).
 *
 * Edit a weighted graph, choose a source and destination, and the backend runs
 * Dijkstra. What this page shows is the backend's own record of the run:
 *   - the iteration table: one snapshot per algorithm iteration, captured while
 *     the algorithm ran (distance and predecessor for every node), which you can
 *     step through forwards and backwards;
 *   - the final routing table: destination, cost, next hop, full path, status;
 *   - the topology, highlighted for the iteration being viewed.
 * The browser performs no path computation. Results are marked out of date as
 * soon as an input changes, and a late response for old inputs is discarded.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Play } from 'lucide-react';
import {
    labsApi, ApiError, type Alt, type Change, type GGraph, type Iteration, type Route,
    type SolveResponse, type WhatIfResponse,
} from '../labs/api';
import {
    changedCells, describeRow, fmtDistance, fmtPred, INF, relaxedLinks, requestKey, stepReducer, treeLinks,
    type StepAction,
} from '../labs/dijkstraView';
import {
    Badge, Button, Card, COLOURS, ErrorBanner, ExecutionBadge, Field, LabPage, Loading, Metric, Notes, Select,
    StaleBanner, useLabResult,
} from '../labs/ui';

const linkId = (a: string, b: string) => [a, b].sort().join('-');
const idOf = (l: { id?: string; a: string; b: string }) => l.id ?? linkId(l.a, l.b);

/** Mirrors the backend rules so obvious mistakes are explained before any request is sent. */
export function validateGraph(g: GGraph, source: string, target: string): string | null {
    if (g.nodes.length < 2) return 'Add at least two nodes.';
    if (!g.nodes.includes(source)) return 'Choose a source node.';
    if (!g.nodes.includes(target)) return 'Choose a destination node.';
    for (const l of g.links) {
        if (!(typeof l.cost === 'number' && Number.isFinite(l.cost) && l.cost > 0)) {
            return `The cost of link ${idOf(l)} must be a number greater than 0.`;
        }
    }
    return null;
}

function GraphView({ graph, route, iteration, source, title, showPath }: {
    graph: GGraph; route?: Route; iteration?: Iteration; source?: string; title: string; showPath: boolean;
}) {
    const n = graph.nodes.length;
    const R = Math.max(105, n * 13);
    const size = 2 * (R + 38);
    const c = size / 2;
    const pos: Record<string, [number, number]> = Object.fromEntries(graph.nodes.map((name, i) => {
        const ang = (2 * Math.PI * i) / Math.max(n, 1) - Math.PI / 2;
        return [name, [c + R * Math.cos(ang), c + R * Math.sin(ang)]];
    }));
    const finalLinks = new Set(showPath ? route?.links ?? [] : []);
    const finalNodes = new Set(showPath ? route?.path ?? [] : []);
    const tree = iteration ? treeLinks(iteration) : new Set<string>();
    const relaxed = iteration ? relaxedLinks(iteration) : new Set<string>();
    const visited = new Set(iteration?.visited ?? route?.visited ?? []);
    return (
        <figure>
            <figcaption className="text-[11px] tracking-[0.12em] uppercase text-white/40 mb-1">{title}</figcaption>
            <svg viewBox={`0 0 ${size} ${size}`} className="w-full max-w-[460px] h-auto" role="img" aria-label={title}>
                {graph.links.map(l => {
                    const id = idOf(l);
                    const [x1, y1] = pos[l.a] ?? [0, 0];
                    const [x2, y2] = pos[l.b] ?? [0, 0];
                    const down = l.up === false;
                    const onFinal = finalLinks.has(id);
                    const hot = relaxed.has(id);
                    const inTree = tree.has(id);
                    const stroke = down ? COLOURS.bad : onFinal ? COLOURS.c : hot ? COLOURS.b : inTree ? COLOURS.a : 'rgba(255,255,255,0.2)';
                    return (
                        <g key={id} data-link={id} data-state={down ? 'down' : onFinal ? 'path' : hot ? 'relaxed' : inTree ? 'tree' : 'idle'}>
                            <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={stroke}
                                strokeWidth={onFinal ? 3.6 : hot || inTree ? 2.2 : 1.3} strokeDasharray={down ? '4 4' : undefined} />
                            <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 3} fontSize="9" textAnchor="middle"
                                fill={down ? COLOURS.bad : 'rgba(255,255,255,0.7)'} stroke="#000" strokeWidth="3"
                                style={{ paintOrder: 'stroke' }}>{l.cost}{down ? ' ✕' : ''}</text>
                        </g>
                    );
                })}
                {graph.nodes.map(name => {
                    const [x, y] = pos[name];
                    const current = iteration?.selected === name;
                    const isVisited = visited.has(name);
                    const onFinal = finalNodes.has(name);
                    const fill = current ? COLOURS.b : onFinal ? COLOURS.c : isVisited ? 'rgba(125,211,252,0.22)' : '#000';
                    const d = iteration ? fmtDistance(iteration.distances[name]) : route ? fmtDistance(route.distances[name]) : '';
                    return (
                        <g key={name} data-node={name} data-state={current ? 'current' : isVisited ? 'visited' : 'unvisited'}>
                            {name === source && <circle cx={x} cy={y} r="19" fill="none" stroke="rgba(255,255,255,0.55)" strokeWidth="1" />}
                            <circle cx={x} cy={y} r="14" fill={fill}
                                stroke={current ? COLOURS.b : isVisited || onFinal ? COLOURS.a : 'rgba(255,255,255,0.4)'} strokeWidth="1.5" />
                            <text x={x} y={y + 4} textAnchor="middle" fontSize="11" fill={current || onFinal ? '#000' : '#fff'}>{name}</text>
                            {d && <text x={x} y={y + 30} textAnchor="middle" fontSize="9" fill="rgba(255,255,255,0.6)"
                                stroke="#000" strokeWidth="3" style={{ paintOrder: 'stroke' }}>d={d}</text>}
                        </g>
                    );
                })}
            </svg>
        </figure>
    );
}

function Legend() {
    const item = (c: string, t: string, dashed = false) => (
        <span className="inline-flex items-center gap-1.5 text-[11px] text-white/50">
            <i className="inline-block w-4 h-0" style={{ borderTop: `2px ${dashed ? 'dashed' : 'solid'} ${c}` }} />{t}
        </span>);
    return (
        <div className="flex flex-wrap gap-x-4 gap-y-1 mt-1">
            <span className="inline-flex items-center gap-1.5 text-[11px] text-white/50"><i className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: COLOURS.b }} />selected now</span>
            <span className="inline-flex items-center gap-1.5 text-[11px] text-white/50"><i className="inline-block w-2.5 h-2.5 rounded-full border" style={{ background: 'rgba(125,211,252,0.22)', borderColor: COLOURS.a }} />visited (final)</span>
            {item(COLOURS.b, 'relaxed now')}{item(COLOURS.a, 'shortest-path tree so far')}{item(COLOURS.c, 'route to destination')}{item(COLOURS.bad, 'link down', true)}
            <span className="text-[11px] text-white/50">d = tentative distance (∞ = no path yet)</span>
        </div>
    );
}

function IterationTable({ nodes, iterations, step, onStep }: {
    nodes: string[]; iterations: Iteration[]; step: number; onStep: (a: StepAction) => void;
}) {
    const rows = iterations.slice(0, step + 1);
    const cur = iterations[step];
    return (
        <div data-testid="iteration-table-section">
            <div className="flex flex-wrap items-center gap-2 mb-2">
                <div className="inline-flex gap-1" role="group" aria-label="Step through iterations">
                    <button aria-label="First iteration" onClick={() => onStep('first')} disabled={step === 0}
                        className="h-8 w-8 rounded-full border border-white/15 text-white/70 disabled:opacity-30 hover:border-white/40 inline-flex items-center justify-center"><ChevronsLeft size={14} /></button>
                    <button aria-label="Previous iteration" onClick={() => onStep('prev')} disabled={step === 0}
                        className="h-8 px-3 rounded-full border border-white/15 text-[12px] text-white/70 disabled:opacity-30 hover:border-white/40 inline-flex items-center gap-1"><ChevronLeft size={14} />Previous</button>
                    <button aria-label="Next iteration" onClick={() => onStep('next')} disabled={step >= iterations.length - 1}
                        className="h-8 px-3 rounded-full bg-white text-black text-[12px] font-medium disabled:opacity-30 inline-flex items-center gap-1">Next<ChevronRight size={14} /></button>
                    <button aria-label="Final iteration" onClick={() => onStep('last')} disabled={step >= iterations.length - 1}
                        className="h-8 w-8 rounded-full border border-white/15 text-white/70 disabled:opacity-30 hover:border-white/40 inline-flex items-center justify-center"><ChevronsRight size={14} /></button>
                </div>
                <span data-testid="step-label" className="text-[12px] text-white/60">
                    Iteration {cur.iteration} of {iterations.length - 1}{cur.iteration === 0 ? ' (initialisation)' : ''}
                </span>
            </div>
            <p data-testid="step-note" className="text-[12px] text-white/70 mb-2">{cur.note}</p>
            {cur.relaxed.length > 0 && (
                <p className="text-[12px] text-white/50 mb-2">
                    Relaxed: {cur.relaxed.map(r =>
                        `${r.to} ${r.old === null ? INF : r.old} → ${r.improved ? r.new : `${r.old} (kept; ${r.new} is not shorter)`} via ${r.via_link}`).join(' · ')}
                </p>
            )}
            <div className="overflow-x-auto border border-white/10 rounded-md">
                <table className="w-full text-[12px] border-collapse" aria-label="Dijkstra iteration table">
                    <thead>
                        <tr className="text-white/40">
                            <th rowSpan={2} className="font-normal text-left px-2 py-1 sticky left-0 bg-black z-10">Iter.</th>
                            <th rowSpan={2} className="font-normal text-left px-2">Selected</th>
                            <th rowSpan={2} className="font-normal text-left px-2">Visited (finalised)</th>
                            <th colSpan={nodes.length} className="font-normal text-center border-l border-white/10 px-2">Distance D(n) from the source</th>
                            <th colSpan={nodes.length} className="font-normal text-center border-l border-white/10 px-2">Predecessor p(n)</th>
                        </tr>
                        <tr className="text-white/50">
                            {nodes.map(n => <th key={`d${n}`} className="font-normal px-2 border-l border-white/5 text-center">{n}</th>)}
                            {nodes.map(n => <th key={`p${n}`} className="font-normal px-2 border-l border-white/5 text-center">{n}</th>)}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((it, k) => {
                            const ch = changedCells(iterations[k - 1], it);
                            const isCur = k === step;
                            return (
                                <tr key={it.iteration} data-testid={`iter-row-${it.iteration}`} data-current={isCur}
                                    onClick={() => onStep({ to: k })}
                                    className={`border-t border-white/5 cursor-pointer ${isCur ? 'bg-white/[0.07]' : 'hover:bg-white/[0.03]'}`}>
                                    <td className="px-2 py-1 sticky left-0 bg-black text-white/60">{it.iteration}</td>
                                    <td className="px-2 text-white">{it.selected ?? '—'}</td>
                                    <td className="px-2 text-white/70">{it.visited.length ? `{${it.visited.join(', ')}}` : '∅'}</td>
                                    {nodes.map(n => {
                                        const d = it.distances[n];
                                        return <td key={`d${n}`} data-cell={`d-${n}`} title={d === null ? 'infinity: no path found yet' : undefined}
                                            className={`px-2 text-center border-l border-white/5 ${ch.dist.has(n) ? 'text-amber-300 font-medium' : d === null ? 'text-white/60' : 'text-white/80'}`}>{fmtDistance(d)}</td>;
                                    })}
                                    {nodes.map(n => (
                                        <td key={`p${n}`} data-cell={`p-${n}`}
                                            className={`px-2 text-center border-l border-white/5 ${ch.pred.has(n) ? 'text-amber-300 font-medium' : 'text-white/60'}`}>{fmtPred(it.predecessors[n])}</td>
                                    ))}
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
            <p className="mt-1 text-[11px] text-white/60">Highlighted cells changed in this iteration. Click a row to jump to it. ∞ means no path is known yet — it is not zero.</p>
        </div>
    );
}

function RoutingTable({ result, source, target, onPick }: {
    result: SolveResponse; source: string; target: string; onPick: (n: string) => void;
}) {
    return (
        <div className="overflow-x-auto border border-white/10 rounded-md">
            <table className="w-full text-[12px]" aria-label="Final routing table">
                <thead><tr className="text-left text-white/40">
                    {['Destination', 'Shortest distance / cost', 'Next hop', 'Complete path', 'Status'].map(h => <th key={h} className="font-normal px-3 py-1.5">{h}</th>)}
                </tr></thead>
                <tbody>
                    {result.routing_table.map(row => {
                        const d = describeRow(row, source);
                        return (
                            <tr key={row.destination} data-testid={`rt-${row.destination}`} data-status={row.status}
                                onClick={() => onPick(row.destination)}
                                className={`border-t border-white/5 cursor-pointer ${row.destination === target ? 'bg-white/[0.07]' : 'hover:bg-white/[0.03]'}`}>
                                <td className="px-3 py-1 text-white">{row.destination}</td>
                                <td className={`px-3 ${row.status === 'unreachable' ? 'text-white/60' : 'text-white/80'}`}>{d.cost}</td>
                                <td className="px-3 text-white/80">{d.next}</td>
                                <td className="px-3 text-white/60">{d.path}</td>
                                <td className="px-3" style={{ color: row.status === 'unreachable' ? COLOURS.bad : row.status === 'source' ? 'rgba(255,255,255,0.5)' : COLOURS.c }}>{d.status}</td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}

function RouteSummary({ route, alts }: { route: Route; alts: Alt[] }) {
    return (
        <div className="space-y-3">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <Metric label="Reachable" value={route.reachable ? 'yes' : 'no'} />
                <Metric label="Total cost" value={route.cost === null ? INF : route.cost} />
                <Metric label="Hops" value={route.reachable ? route.links.length : '—'} />
                <Metric label="Nodes settled" value={route.visited.length} />
            </div>
            <p className="text-[13px] text-white/70">
                {route.reachable ? route.path.join(' → ') : `${route.target} is unreachable from ${route.source}.`}
            </p>
            {alts.length > 0 && (
                <ol className="space-y-1">
                    {alts.map((a, i) => (
                        <li key={i} className="text-[12px] text-white/60">
                            {i === 0 ? 'best' : `#${i + 1}`}: {a.path.join(' → ')} <span className="text-white/60">(cost {a.cost})</span>
                        </li>
                    ))}
                </ol>
            )}
        </div>
    );
}

export default function RoutingLabPage() {
    const [graph, setGraph] = useState<GGraph | null>(null);
    const [source, setSource] = useState('A');
    const [target, setTarget] = useState('F');
    const [auto, setAuto] = useState(true);
    const [step, setStep] = useState(0);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [whatIf, setWhatIf] = useState<WhatIfResponse | null>(null);
    const [newNode, setNewNode] = useState('');
    const [nl, setNl] = useState({ a: '', b: '', cost: 1 });
    const [change, setChange] = useState<{ kind: Change['kind']; link: string; cost: number }>({ kind: 'link_down', link: 'B-D', cost: 10 });
    const reqId = useRef(0);

    const key = graph ? requestKey(graph, source, target) : '';
    const solved = useLabResult<SolveResponse>(key);
    const validation = graph ? validateGraph(graph, source, target) : null;

    useEffect(() => {
        labsApi.routingExample()
            .then(ex => { setGraph(ex.graph); setSource(ex.source); setTarget(ex.target); })
            .catch(e => setLoadError(e instanceof ApiError ? e.message : String(e)));
    }, []);

    const clean = (g: GGraph): GGraph => ({ nodes: g.nodes, links: g.links.map(({ id, ...rest }) => rest) });

    const solve = useCallback(async () => {
        if (!graph || validateGraph(graph, source, target)) return;
        const id = ++reqId.current;
        const forKey = requestKey(graph, source, target);
        setLoading(true);
        setError(null);
        setWhatIf(null);
        try {
            const out = await labsApi.solve(clean(graph), source, target);
            if (id !== reqId.current) return;               // inputs changed again: drop this response
            solved.set(out, forKey);
            setStep(out.iterations.length - 1);
        } catch (e) {
            if (id === reqId.current) setError(e instanceof ApiError ? e.message : String(e));
        } finally {
            if (id === reqId.current) setLoading(false);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [graph, source, target]);

    const solveRef = useRef(solve);
    solveRef.current = solve;
    useEffect(() => {
        if (!auto || !graph || validation) return;
        const t = setTimeout(() => solveRef.current(), 250);
        return () => clearTimeout(t);
    }, [auto, key, graph === null, validation]);       // eslint-disable-line react-hooks/exhaustive-deps

    if (!graph) {
        return (
            <LabPage title="Dijkstra Shortest-Path Routing" experiment="routing"
                subtitle="Dijkstra shortest-path routing simulator."
                back={{ to: '/features', label: 'Back to features' }}>
                {loadError ? <ErrorBanner error={loadError} onClose={() => setLoadError(null)} /> : <Loading what="Loading the example topology…" />}
            </LabPage>
        );
    }

    const edit = (g: GGraph) => { setGraph(g); setWhatIf(null); setError(null); };
    const nodeOpts = graph.nodes.map(n => [n, n] as [string, string]);
    const linkOpts = graph.links.map(l => [idOf(l), idOf(l)] as [string, string]);
    const result = solved.result;
    const it = result?.iterations[step];
    const atFinal = !!result && step === result.iterations.length - 1;
    const onStep = (a: StepAction) => result && setStep(s => stepReducer(s, a, result.iterations.length));

    const addNode = () => {
        const name = newNode.trim();
        if (!name) return setError('Enter a node name.');
        if (graph.nodes.includes(name)) return setError(`Node ${name} already exists.`);
        edit({ ...graph, nodes: [...graph.nodes, name] });
        setNewNode('');
    };
    const removeNode = (name: string) => {
        const nodes = graph.nodes.filter(n => n !== name);
        edit({ nodes, links: graph.links.filter(l => l.a !== name && l.b !== name) });
        if (source === name) setSource(nodes[0] ?? '');
        if (target === name) setTarget(nodes.find(n => n !== (source === name ? nodes[0] : source)) ?? nodes[0] ?? '');
    };
    const addLink = () => {
        if (!nl.a || !nl.b || nl.a === nl.b) return setError('Choose two different nodes for the link.');
        if (!(nl.cost > 0)) return setError('Link cost must be greater than 0.');
        const id = linkId(nl.a, nl.b);
        if (graph.links.some(l => idOf(l) === id)) return setError(`Link ${id} already exists.`);
        const [a, b] = [nl.a, nl.b].sort();
        edit({ ...graph, links: [...graph.links, { id, a, b, cost: nl.cost, delay_ms: 5, loss: 0, bandwidth_mbps: 100, up: true }] });
    };
    const patchLink = (id: string, patch: Partial<GGraph['links'][number]>) =>
        edit({ ...graph, links: graph.links.map(l => idOf(l) === id ? { ...l, ...patch } : l) });
    const removeLink = (id: string) => edit({ ...graph, links: graph.links.filter(l => idOf(l) !== id) });

    const runWhatIf = async () => {
        if (validation) return setError(validation);
        const [a, b] = change.link.split('-');
        const ch: Change = change.kind === 'cost_change' ? { kind: 'cost_change', a, b, cost: change.cost } : { kind: change.kind, a, b };
        setError(null);
        try { setWhatIf(await labsApi.whatIf(clean(graph), source, target, [ch])); }
        catch (e) { setError(e instanceof ApiError ? e.message : String(e)); }
    };

    return (
        <LabPage title="Dijkstra Shortest-Path Routing" experiment="routing"
            subtitle="Dijkstra's algorithm computes the least-cost path from one source to every node of a weighted graph. A routing table is derived from that result: for each destination, the cost and the first node (next hop) on the path. Dijkstra is an algorithm, not a routing protocol — link-state protocols such as OSPF run it on a link-state database, and distance-vector protocols such as RIP use a different algorithm (Bellman-Ford); distance-vector routing is not implemented in this build. This lab changes no real routing table. Cost is a unitless administrative metric; the smallest sum wins, and equal totals are resolved by the lowest node name."
            back={{ to: '/features', label: 'Back to features' }}>
            <ErrorBanner error={error} onClose={() => setError(null)} />

            <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
                <Card title="1 · Network" badge={<Badge kind="simulation" />}>
                    <GraphView graph={graph} route={result?.route} iteration={result && !solved.stale ? it : undefined}
                        source={source} showPath={atFinal && !solved.stale}
                        title={result && !solved.stale ? `Topology — iteration ${it?.iteration} of ${result.iterations.length - 1}` : 'Topology'} />
                    <Legend />
                    <div className="mt-3 flex flex-wrap gap-1.5">
                        {graph.nodes.map(n => (
                            <span key={n} className="inline-flex items-center gap-1 h-7 pl-2.5 pr-1 rounded-full border border-white/15 text-[12px] text-white/70">
                                {n}
                                <button onClick={() => removeNode(n)} aria-label={`Remove node ${n}`} className="w-5 h-5 rounded-full text-white/40 hover:text-red-300">✕</button>
                            </span>
                        ))}
                    </div>
                    <div className="mt-3 flex items-end gap-2">
                        <label className="flex-1 block text-[11px] text-white/50">
                            <span className="block mb-1">New node</span>
                            <input value={newNode} maxLength={24} onChange={e => setNewNode(e.target.value)} aria-label="New node name"
                                className="w-full h-9 px-2 rounded-md bg-black border border-white/15 text-white text-[13px]" />
                        </label>
                        <Button variant="ghost" onClick={addNode}>Add node</Button>
                    </div>
                </Card>

                <Card title="Links (weight and availability)">
                    <div className="overflow-x-auto">
                        <table className="w-full text-[12px]">
                            <thead><tr className="text-left text-white/40">
                                <th className="py-1 pr-3 font-normal">Link</th><th className="pr-3 font-normal">Cost</th><th className="pr-3 font-normal">Enabled</th><th /></tr></thead>
                            <tbody>
                                {graph.links.map(l => {
                                    const id = idOf(l);
                                    return (
                                        <tr key={id} className="border-t border-white/5">
                                            <td className={`py-1 pr-3 ${l.up === false ? 'text-red-300/80 line-through' : 'text-white/70'}`}>{id}</td>
                                            <td className="pr-3">
                                                <input type="number" min={0.001} step="any" value={Number.isFinite(l.cost) ? l.cost : ''}
                                                    aria-label={`Cost of ${id}`}
                                                    onChange={e => patchLink(id, { cost: e.target.value === '' ? NaN : Number(e.target.value) })}
                                                    className="w-20 h-7 px-2 rounded bg-black border border-white/15 text-white" />
                                            </td>
                                            <td className="pr-3">
                                                <input type="checkbox" checked={l.up !== false} aria-label={`Link ${id} enabled`}
                                                    onChange={e => patchLink(id, { up: e.target.checked })} />
                                            </td>
                                            <td><button onClick={() => removeLink(id)} aria-label={`Remove link ${id}`} className="text-white/40 hover:text-red-300">✕</button></td>
                                        </tr>
                                    );
                                })}
                                {graph.links.length === 0 && <tr><td colSpan={4} className="py-2 text-white/40">No links yet — every node is isolated.</td></tr>}
                            </tbody>
                        </table>
                    </div>
                    <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2 items-end">
                        <Select label="From" value={nl.a} onChange={v => setNl({ ...nl, a: v })} options={[['', '—'], ...nodeOpts]} />
                        <Select label="To" value={nl.b} onChange={v => setNl({ ...nl, b: v })} options={[['', '—'], ...nodeOpts]} />
                        <Field label="Cost" value={nl.cost} min={0.001} onChange={v => setNl({ ...nl, cost: v })} />
                        <Button variant="ghost" onClick={addLink}>Add link</Button>
                    </div>
                    <p className="mt-2 text-[11px] text-white/60">Costs must be finite and greater than 0. Links are bidirectional with one cost. A disabled link behaves as if it were absent.</p>
                </Card>
            </div>

            <Card title="2 · Source, destination and run">
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 items-end">
                    <Select label="Source (table is computed from here)" value={source} onChange={setSource} options={nodeOpts} />
                    <Select label="Destination (route highlighted)" value={target} onChange={setTarget} options={nodeOpts} />
                    <Button onClick={() => solve()} disabled={loading || !!validation}><Play size={12} /> {loading ? 'Running…' : 'Run Dijkstra'}</Button>
                    <label className="flex items-center gap-2 text-[12px] text-white/60 pb-2">
                        <input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} /> Recalculate automatically on every change
                    </label>
                </div>
                {validation && <p role="alert" data-testid="validation" className="mt-3 text-[12px] text-amber-300">{validation}</p>}
                {!result && !loading && !validation && <p className="mt-3 text-[12px] text-white/45">No result yet — press “Run Dijkstra”{auto ? '' : ' (auto-recalculate is off)'}.</p>}
                {loading && <div className="mt-3"><Loading what="Running Dijkstra…" /></div>}
            </Card>

            {result && (
                <div className={`space-y-5 ${loading ? 'opacity-60' : ''}`} data-testid="results" data-stale={solved.stale}>
                    {/* With auto-recalculate on, a fresh result is already on its way; the banner is only
                        needed when it is not (auto off, invalid inputs, or the last run failed). */}
                    <StaleBanner stale={solved.stale && (!auto || !!validation || !!error)}
                        onRerun={validation ? undefined : () => solve()} />
                    <ExecutionBadge execution={result.execution} />

                    <Card title="3 · Iteration table (recorded while the algorithm ran)">
                        <IterationTable nodes={graph.nodes.filter(n => n in result.iterations[0].distances)}
                            iterations={result.iterations} step={step} onStep={onStep} />
                    </Card>

                    <Card title={`4 · Final routing table for source ${result.route.source}`}>
                        {atFinal ? (
                            <>
                                <RoutingTable result={result} source={result.route.source} target={target} onPick={setTarget} />
                                <p className="mt-1 text-[11px] text-white/60">Next hop = the first node after the source on the shortest path. Click a row to highlight its path.</p>
                            </>
                        ) : (
                            <p className="text-[12px] text-white/50">
                                The algorithm has not finished at iteration {step}. {' '}
                                <button onClick={() => onStep('last')} className="underline text-white/80">Jump to the final iteration</button> to see the routing table.
                            </p>
                        )}
                    </Card>

                    <Card title="Selected route (final result of the run)">
                        <RouteSummary route={result.route} alts={result.alternatives} />
                    </Card>
                </div>
            )}

            <Card title="5 · Introduce a change: compare before / after" badge={<Badge kind="simulation" />}>
                <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 items-end">
                    <Select label="Change" value={change.kind} onChange={v => setChange({ ...change, kind: v as Change['kind'] })}
                        options={[['link_down', 'Link failure'], ['link_up', 'Link restored'], ['cost_change', 'Cost change']]} />
                    <Select label="Link" value={change.link} onChange={v => setChange({ ...change, link: v })} options={linkOpts} />
                    {change.kind === 'cost_change' && <Field label="New cost" value={change.cost} min={0.001} onChange={v => setChange({ ...change, cost: v })} />}
                    <Button onClick={runWhatIf} disabled={!!validation || graph.links.length === 0}>Compare before / after</Button>
                </div>
                {whatIf && (
                    <div className="mt-5 space-y-4">
                        <p className="text-[13px] text-white/80">{whatIf.summary}</p>
                        <div className="grid gap-5 md:grid-cols-2">
                            <div><GraphView graph={whatIf.before.graph} route={whatIf.before.route} title="Before" showPath source={source} /><RouteSummary route={whatIf.before.route} alts={whatIf.before.alternatives} /></div>
                            <div><GraphView graph={whatIf.after.graph} route={whatIf.after.route} title="After" showPath source={source} /><RouteSummary route={whatIf.after.route} alts={whatIf.after.alternatives} /></div>
                        </div>
                        <ExecutionBadge execution={whatIf.execution} />
                    </div>
                )}
            </Card>

            <Notes title="Model notes" items={[
                "Dijkstra's algorithm (E. W. Dijkstra, 1959); each iteration selects the unvisited node with the smallest tentative distance, ties broken by lowest node name.",
                'Alternatives use Yen\'s k-shortest loopless paths; fewer than three listed means fewer loopless routes exist.',
                'A disabled link is ignored exactly as if it were absent; an unreachable destination keeps distance ∞ and is reported, not hidden.',
                'This is a graph computation. It measures nothing and changes no real routing table.',
            ]} />
        </LabPage>
    );
}
