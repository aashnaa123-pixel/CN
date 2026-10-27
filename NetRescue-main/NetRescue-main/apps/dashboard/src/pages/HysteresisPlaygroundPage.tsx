/**
 * HysteresisPlaygroundPage — Feature A as a workbench.
 *
 * Build a network, break a connection, watch NetRescue find another way round.
 *
 * The rule that shapes every panel here: nothing on this screen claims something
 * the backend did not report. A node is red because the API said `failed: true`,
 * and the API only says that after the listener was closed. A route is drawn
 * because the routing tables produced it. A packet walks a path because that is
 * the path `last_delivery` came back on. Where a number is not measured — and
 * latency, throughput and loss are not measured anywhere in this lab — the field
 * is simply absent rather than filled in.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { RunGuide } from '../labs/ui';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { ArrowLeft, Play, Trash2, AlertTriangle, CheckCircle2, Radio } from 'lucide-react';
import TopologyCanvas, { NODE_RADIUS } from '../playground/TopologyCanvas';
import type { Selection, PacketRun } from '../playground/TopologyCanvas';
import { ApiError, download, playgroundApi } from '../playground/api';
import type {
    CanvasLink,
    CanvasNode,
    DetectorComparison,
    DrawnNode,
    ExperimentEvent,
    PlaygroundState,
} from '../playground/types';

const ROLES = ['source', 'router', 'destination'] as const;

/** What a topology edit sends to PUT /api/playground/topology. */
interface SavePayload {
    nodes: DrawnNode[];
    links: { node_a: string; node_b: string }[];
    source: string | null;
    destination: string | null;
}

/** An edit expressed as a pure change to the current topology. */
type TopologyMutator = (
    nodes: PlaygroundState['topology']['nodes'],
    links: PlaygroundState['topology']['links'],
) => { nodes: PlaygroundState['topology']['nodes']; links: PlaygroundState['topology']['links'] };

type Runner = (
    label: string,
    action: () => Promise<PlaygroundState | void>,
) => Promise<PlaygroundState | null>;

const SEVERITY_TONE: Record<string, string> = {
    info: 'text-white/55',
    warning: 'text-amber-300/90',
    error: 'text-red-400/90',
};

const DETECTOR_TONE: Record<string, string> = {
    HEALTHY: '#2dd4bf',
    SUSPECT: '#f5b54a',
    FAILED: '#ef4444',
    UNKNOWN: 'rgba(255,255,255,0.35)',
};

/** Preset heartbeat *inputs*. The results come from replaying these. */
const SEQUENCES: Record<string, { label: string; hint?: string; values: boolean[] }> = {
    flapping: {
        label: 'Quality oscillating around the threshold',
        values: [true, true, true, false, false, false, false, true, true, true, true,
            false, false, false, false, true, true, true, true, true],
    },
    outage: {
        label: 'Clean failure and recovery',
        hint: 'A sustained outage followed by a sustained recovery.',
        values: [true, true, true, true, false, false, false, false, false, false,
            false, false, true, true, true, true, true, true, true, true],
    },
    brief: {
        label: 'Brief blip that never becomes a failure',
        hint: 'A gap shorter than F_thresh — the dead band absorbs it.',
        values: [true, true, true, false, false, true, true, true, true, true, true,
            true, true, true, true],
    },
};

export default function HysteresisPlaygroundPage() {
    const [state, setState] = useState<PlaygroundState | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [selection, setSelection] = useState<Selection>({ kind: null, id: null });
    const [connectingFrom, setConnectingFrom] = useState<string | null>(null);
    const [packet, setPacket] = useState<PacketRun | null>(null);
    const [flash, setFlash] = useState<string | null>(null);

    useEffect(() => {
        document.title = 'Hysteresis Recovery Playground — NetRescue';
    }, []);

    const refresh = useCallback(async () => {
        try {
            setState(await playgroundApi.state());
            setError(null);
        } catch (exc) {
            setError(message(exc));
        }
    }, []);

    useEffect(() => {
        refresh();
        // The detectors run on their own clock. While a network is up, the
        // readings change without anyone clicking, so the panels poll — but only
        // while something is running, so an idle page costs nothing.
        const timer = window.setInterval(() => {
            if (document.visibilityState === 'visible') refresh();
        }, 3000);
        return () => window.clearInterval(timer);
    }, [refresh]);

    // -- actions ---------------------------------------------------------

    const run = useCallback(
        async (label: string, action: () => Promise<PlaygroundState | void>) => {
            setBusy(label);
            try {
                const next = await action();
                if (next) setState(next);
                else await refresh();
                setError(null);
                return next ?? null;
            } catch (exc) {
                setError(message(exc));
                // A refused action must not leave the canvas showing the
                // operator's intent; re-read what the server actually holds.
                await refresh();
                return null;
            } finally {
                setBusy(null);
            }
        },
        [refresh],
    );

    const save = useCallback(
        async (topology: SavePayload) => run('save', () => playgroundApi.save(topology)),
        [run],
    );

    const selectedNode: CanvasNode | undefined =
        state?.nodes.find(n => n.node_id === selection.id) ?? undefined;
    const selectedLink: CanvasLink | undefined =
        state?.links.find(l => l.link_id === selection.id) ?? undefined;

    // -- editing (optimistic on the canvas, authoritative on the server) --

    const patchTopology = useCallback(
        async (mutate: TopologyMutator, source: string | null, destination: string | null) => {
            if (!state) return null;
            const next = mutate(state.topology.nodes, state.topology.links);
            return save({
                nodes: next.nodes.map(n => ({
                    node_id: n.node_id, label: n.label, role: n.role, x: n.x, y: n.y,
                })),
                links: next.links.map(l => ({ node_a: l.node_a, node_b: l.node_b })),
                source,
                destination,
            });
        },
        [state, save],
    );

    const onMoveNode = useCallback((id: string, x: number, y: number) => {
        if (!state) return;
        // Positions move locally while the pointer is down; they are written to
        // the server once the drag settles (see the effect below). Writing on
        // every pointer event would be a request per frame for something the
        // operator cannot see change.
        setState({
            ...state,
            topology: {
                ...state.topology,
                nodes: state.topology.nodes.map(n =>
                    n.node_id === id
                        ? {
                            ...n,
                            x: Math.round(clamp(x, NODE_RADIUS + 8, 1000 - NODE_RADIUS - 8)),
                            y: Math.round(clamp(y, NODE_RADIUS + 18, 440 - NODE_RADIUS - 18)),
                        }
                        : n,
                ),
            },
        });
    }, [state]);

    // Persist node positions once a drag settles. A position is part of the
    // topology the operator drew, so it is saved like any other change rather
    // than kept in a component that dies on reload.
    const savedPositions = useRef('');
    useEffect(() => {
        if (!state) return;
        const signature = state.topology.nodes.map(n => `${n.node_id}:${n.x},${n.y}`).join('|');
        if (signature === savedPositions.current) return;

        const timer = window.setTimeout(() => {
            savedPositions.current = signature;
            save({
                nodes: state.topology.nodes.map(n => ({
                    node_id: n.node_id, label: n.label, role: n.role, x: n.x, y: n.y,
                })),
                links: state.topology.links.map(l => ({ node_a: l.node_a, node_b: l.node_b })),
                source: state.source,
                destination: state.destination,
            }).catch(() => undefined);
        }, 700);
        return () => window.clearTimeout(timer);
    }, [state, save]);

    const onConnectComplete = useCallback(
        async (from: string, to: string) => {
            if (!state) return;
            const key = [from, to].sort().join('-');
            if (state.topology.links.some(l => [l.node_a, l.node_b].sort().join('-') === key)) {
                setError(`${from} and ${to} are already connected. Delete that link first, or draw a different one.`);
                return;
            }
            await patchTopology((nodes, links) => ({ nodes, links: [...links, { link_id: key, node_a: from, node_b: to }] }),
                state.source, state.destination);
        },
        [state, patchTopology],
    );

    const onTest = useCallback(async () => {
        if (!state?.source || !state?.destination) {
            setError('Mark one node as the source and one as the destination first.');
            return;
        }
        setBusy('test');
        try {
            const result = await playgroundApi.test(state.source, state.destination);
            setPacket({ path: result.path ?? [], key: Date.now(), delivered: result.delivered });
            setFlash(
                result.delivered
                    ? `Delivered ${state.source} → ${state.destination} via ${(result.path ?? []).join(' → ')}`
                    : `Not delivered — ${result.reason}`,
            );
        } catch (exc) {
            setError(message(exc));
        } finally {
            setBusy(null);
            await refresh();
        }
    }, [state, refresh]);

    const resetWorkspace = useCallback(async () => {
        if (!window.confirm('Reset the workspace? Stops the running network and empties the topology.')) return;
        setSelection({ kind: null, id: null });
        setPacket(null);
        await run('reset', async () => {
            const state = await playgroundApi.clear();
            await playgroundApi.clearEvents();
            return state;
        });
    }, [run]);

    if (!state) {
        return (
            <main className="pt-20 min-h-screen px-4 sm:px-6 md:px-8">
                <Header onReset={resetWorkspace} onTest={onTest} busy={busy} state={null} />
                <RunGuide experiment="hysteresis" />
                <div className="mt-8 border border-white/10 rounded-lg p-10 text-center">
                    {error ? (
                        <p className="text-red-400/90 text-[14px]">{error}</p>
                    ) : (
                        <p className="text-white/40 text-[14px]">Contacting the NetRescue API…</p>
                    )}
                </div>
            </main>
        );
    }

    const statusLine = describeStatus(state);

    return (
        <main className="pt-20 px-3 sm:px-6 md:px-8 pb-16">
            <Header
                onReset={resetWorkspace}
                onTest={onTest}
                busy={busy}
                state={state}
            />
            <RunGuide experiment="hysteresis" />

            {error && (
                <div role="alert"
                    className="mt-4 flex items-start gap-2 border border-red-500/40 bg-red-950/20 rounded-md px-4 py-3 text-[13px] text-red-300">
                    <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                    <span className="flex-1">{error}</span>
                    <button onClick={() => setError(null)} className="text-red-300/60 hover:text-red-200">✕</button>
                </div>
            )}
            {flash && (
                <div className="mt-4 border border-white/10 bg-white/[0.03] rounded-md px-4 py-3 text-[13px] text-white/70">
                    {flash}
                </div>
            )}

            {/* The drift banner. Shown whenever the drawing and the running
                network disagree — after an edit, or after the runtime was
                changed from the console. */}
            {state.running && !state.draft_is_running && (
                <div className="mt-4 border border-amber-500/40 bg-amber-950/10 rounded-md px-4 py-3 text-[13px] text-amber-200/90">
                    The topology has been edited. The running network still has the
                    previous shape — press <strong>Apply to Lab</strong> to make the
                    network match what is on the canvas.
                </div>
            )}

            <div className="mt-5 grid grid-cols-1 xl:grid-cols-[300px_minmax(0,1fr)_340px] gap-4">
                <LeftPanel
                    state={state}
                    busy={busy}
                    connectingFrom={connectingFrom}
                    onConnectCancel={() => setConnectingFrom(null)}
                    onPatch={patchTopology}
                    onValidate={() =>
                        run('validate', () => playgroundApi.validate(state.source, state.destination))
                    }
                    onExample={() => run('example', () => playgroundApi.example())}
                    onClear={resetWorkspace}
                    onApply={(options) => run('apply', () => playgroundApi.apply(options))}
                    onTest={onTest}
                />

                <section className="order-first xl:order-none border border-white/10 rounded-lg overflow-hidden bg-black/40">
                    <PanelBar
                        title="Network canvas"
                        right={statusLine.badge}
                        rightTone={statusLine.tone}
                    />
                    <div className="h-[440px] xl:h-[560px]">
                        <TopologyCanvas
                            nodes={state.nodes}
                            links={state.links}
                            route={state.route}
                            previousRoute={state.previous_route}
                            source={state.source}
                            destination={state.destination}
                            selection={selection}
                            connectingFrom={connectingFrom}
                            packet={packet}
                            onSelectNode={id => setSelection({ kind: 'node', id })}
                            onSelectLink={id => setSelection({ kind: 'link', id })}
                            onSelectEmpty={() => setSelection({ kind: null, id: null })}
                            onMoveNode={onMoveNode}
                            onConnectStart={id => setConnectingFrom(id === connectingFrom ? null : id)}
                            onConnectComplete={onConnectComplete}
                            onConnectCancel={() => setConnectingFrom(null)}
                        />
                    </div>
                    <ValidationBar state={state} />
                </section>

                <RightPanel
                    state={state}
                    busy={busy}
                    node={selectedNode}
                    link={selectedLink}
                    onFailNode={id => run(`fail-${id}`, () => playgroundApi.failNode(id))}
                    onRestoreNode={id => run(`restore-${id}`, () => playgroundApi.restoreNode(id))}
                    onFailLink={id => run(`fail-${id}`, () => playgroundApi.failLink(id))}
                    onRestoreLink={id => run(`restore-${id}`, () => playgroundApi.restoreLink(id))}
                    onTest={onTest}
                    onSelectLink={id => setSelection({ kind: 'link', id })}
                    onHighlight={() => setFlash(
                        selectedLink
                            ? `${selectedLink.link_id}: ${selectedLink.issues.length
                                ? selectedLink.issues.map(i => i.message).join(' ')
                                : selectedLink.notes.map(n => n.message).join(' ') || 'no findings'}`
                            : 'Select a link first.',
                    )}
                />
            </div>

            <div className="mt-4 grid grid-cols-1 lg:grid-cols-3 gap-4 items-start">
                <EventTimeline
                    events={state.events}
                    onClear={() => run('clear-events', () => playgroundApi.clearEvents())}
                    onExport={async fmt => {
                        const file = await playgroundApi.export(fmt);
                        download(file.filename, file.content,
                            fmt === 'csv' ? 'text/csv' : 'application/json');
                    }}
                />
                <ConnectivityPanel state={state} />
                <HysteresisPanel
                    state={state}
                    busy={busy}
                    onRun={run}
                    onSelectLink={id => setSelection({ kind: 'link', id })}
                />
            </div>
        </main>
    );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function Header({
    onReset, onTest, busy, state,
}: {
    onReset: () => void;
    onTest: () => void;
    busy: string | null;
    state: PlaygroundState | null;
}) {
    const status = state ? describeStatus(state) : null;
    return (
        <motion.header
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3 }}
            className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between"
        >
            <div>
                <Link
                    to="/features/feature-a"
                    className="inline-flex items-center gap-2 text-[12px] text-white/40 hover:text-white/70 transition-colors"
                >
                    <ArrowLeft size={13} /> Back to Hysteresis Recovery
                </Link>
                <h1
                    className="mt-2 font-light tracking-[-0.03em] text-white"
                    style={{ fontSize: 'clamp(24px, 3.2vw, 36px)' }}
                >
                    HYSTERESIS RECOVERY
                </h1>
                <p className="text-white/45 text-[13px] sm:text-[14px] mt-1">
                    Build a network. Break a connection. Watch NetRescue recover.
                </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
                <StatusReadout state={state} />
                <button
                    onClick={onReset}
                    disabled={busy !== null}
                    className="h-9 px-4 rounded-full border border-white/15 text-[13px] text-white/70 hover:border-white/40 hover:text-white disabled:opacity-40 transition-colors"
                >
                    Reset Workspace
                </button>
                <button
                    onClick={onTest}
                    disabled={busy !== null || !state?.running}
                    title={state?.running ? 'Send a packet through the running network' : 'Apply a topology first'}
                    className="h-9 px-4 rounded-full bg-white text-black text-[13px] font-medium hover:bg-white/90 disabled:opacity-30 transition-colors flex items-center gap-2"
                >
                    <Play size={12} /> {busy === 'test' ? 'Testing…' : 'Run Connectivity Test'}
                </button>
            </div>
        </motion.header>
    );
}

function StatusReadout({ state }: { state: PlaygroundState | null }) {
    const status = state ? describeStatus(state) : null;
    return (
        <div className="flex items-center gap-2 h-9 px-4 rounded-full border border-white/10 bg-white/[0.03]">
            <span
                className="w-1.5 h-1.5 rounded-full"
                style={{ background: status?.color ?? 'rgba(255,255,255,0.3)' }}
            />
            <span className="text-[12px] text-white/60">{status?.label ?? 'Connecting'}</span>
        </div>
    );
}

function describeStatus(state: PlaygroundState) {
    if (!state.running) {
        return {
            label: 'No network running',
            badge: 'DRAFT',
            tone: 'rgba(255,255,255,0.35)',
            color: 'rgba(255,255,255,0.3)',
        };
    }
    if (!state.validation.routable) {
        return {
            label: state.draft_is_running ? 'Destination unreachable' : 'Draft differs from network',
            badge: 'UNREACHABLE',
            tone: '#ef4444',
            color: '#ef4444',
        };
    }
    if (state.packets.failed > 0) {
        return {
            label: `Running · ${state.packets.delivered}/${state.packets.sent} delivered`,
            badge: 'DEGRADED',
            tone: '#f5b54a',
            color: '#f5b54a',
        };
    }
    return {
        label: `Running · ${state.packets.delivered}/${state.packets.sent} delivered`,
        badge: 'ONLINE',
        tone: '#2dd4bf',
        color: '#2dd4bf',
    };
}

// ---------------------------------------------------------------------------
// Left panel — building
// ---------------------------------------------------------------------------

function LeftPanel({
    state, busy, connectingFrom, onConnectCancel, onPatch, onValidate,
    onExample, onClear, onApply, onTest,
}: {
    state: PlaygroundState;
    busy: string | null;
    connectingFrom: string | null;
    onConnectCancel: () => void;
    onPatch: (
        mutate: TopologyMutator,
        source: string | null,
        destination: string | null,
    ) => Promise<PlaygroundState | null>;
    onValidate: () => void;
    onExample: () => void;
    onClear: () => void;
    onApply: (options: {
        heartbeat_interval_s: number; f_thresh: number; n: number; r_thresh: number;
    }) => void;
    onTest: () => void;
}) {
    const [nodeId, setNodeId] = useState('');
    const [nodeLabel, setNodeLabel] = useState('');
    const [nodeRole, setNodeRole] = useState<(typeof ROLES)[number]>('router');
    const [heartbeat, setHeartbeat] = useState(0.5);
    const [fThresh, setFThresh] = useState(3);
    const [nThresh, setNThresh] = useState(5);
    const [rThresh, setRThresh] = useState(5);

    const addNode = async () => {
        const id = nodeId.trim().toUpperCase();
        if (!id) return;
        if (!/^[A-Za-z0-9_]{1,32}$/.test(id)) {
            // The server is the authority, but saying so here saves a round trip
            // for a rule that is fixed and stated in the same breath.
            alert(`"${id}" cannot be a node id. Use 1–32 characters: letters, digits and underscore.`);
            return;
        }
        if (state.topology.nodes.some(n => n.node_id === id)) {
            alert(`Node ${id} already exists. Node ids must be unique.`);
            return;
        }
        if (state.topology.nodes.length >= state.max_nodes) {
            alert(`This lab supports at most ${state.max_nodes} nodes.`);
            return;
        }
        const index = state.topology.nodes.length;
        await onPatch(
            (nodes, links) => ({
                nodes: [...nodes, {
                    node_id: id,
                    label: nodeLabel.trim() || id,
                    role: nodeRole,
                    x: 120 + (index % 4) * 230,
                    y: 110 + Math.floor(index / 4) * 200,
                }],
                links,
            }),
            state.source, state.destination,
        );
        setNodeId('');
        setNodeLabel('');
    };

    return (
        <section className="border border-white/10 rounded-lg bg-black/40 flex flex-col">
            <PanelBar title="Build" />

            <div className="p-4 space-y-5">
                {/* Add node */}
                <section>
                    <SubLabel>Add node</SubLabel>
                    <div className="grid grid-cols-[1fr_1fr] gap-2">
                        <Field label="ID">
                            <input value={nodeId} onChange={e => setNodeId(e.target.value)}
                                placeholder="F" maxLength={32}
                                className={inputClass} />
                        </Field>
                        <Field label="Role">
                            <select value={nodeRole} onChange={e => setNodeRole(e.target.value as typeof nodeRole)}
                                className={inputClass}>
                                {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                            </select>
                        </Field>
                    </div>
                    <div className="mt-2">
                        <Field label="Label (optional)">
                            <input value={nodeLabel} onChange={e => setNodeLabel(e.target.value)}
                                placeholder="Router F" maxLength={64} className={inputClass} />
                        </Field>
                    </div>
                    <button onClick={addNode} className={`${primaryBtn} mt-2 w-full`}>
                        + Add node
                    </button>
                </section>

                {/* Connect */}
                <section>
                    <SubLabel>Connect</SubLabel>
                    {connectingFrom && (
                        <div className="border border-white/20 rounded-md p-3 text-[12px] text-white/70">
                            Drawing from <strong className="text-white">{connectingFrom}</strong>.
                            Click another node to complete it.
                            <button onClick={onConnectCancel} className="block mt-2 text-white/50 hover:text-white">
                                Cancel (Esc)
                            </button>
                        </div>
                    )}
                    {state.links.length > 0 && (
                        <ul className="mt-2 space-y-1 max-h-32 overflow-y-auto">
                            {state.links.map(l => (
                                <li key={l.link_id}
                                    className="flex items-center justify-between text-[12px] text-white/55">
                                    <span>{l.node_a} ↔ {l.node_b}</span>
                                    <button
                                        onClick={() => onPatch(
                                            (nodes, links) => ({
                                                nodes,
                                                links: links.filter(k => k.link_id !== l.link_id),
                                            }),
                                            state.source, state.destination,
                                        )}
                                        className="text-white/60 hover:text-red-400"
                                        aria-label={`Delete link ${l.link_id}`}
                                    >
                                        <Trash2 size={12} />
                                    </button>
                                </li>
                            ))}
                        </ul>
                    )}
                </section>

                {/* Validation */}
                <section>
                    <SubLabel>Validate</SubLabel>
                    <div className="flex gap-2">
                        <button onClick={onValidate} disabled={busy !== null} className={`${ghostBtn} flex-1`}>
                            Validate network
                        </button>
                        <button onClick={onClear} disabled={busy !== null} className={ghostBtn}>
                            Clear
                        </button>
                    </div>
                    <button onClick={onExample} disabled={busy !== null} className={`${ghostBtn} mt-2 w-full`}>
                        Load example network
                    </button>
                </section>

                {/* Run */}
                <section>
                    <SubLabel>Bring it up</SubLabel>
                    <div className="grid grid-cols-2 gap-2">
                        <Field label="F_thresh">
                            <input type="number" min={1} max={50} value={fThresh}
                                onChange={e => setFThresh(Number(e.target.value))} className={inputClass} />
                        </Field>
                        <Field label="N">
                            <input type="number" min={1} max={100} value={nThresh}
                                onChange={e => setNThresh(Number(e.target.value))} className={inputClass} />
                        </Field>
                        <Field label="R_thresh">
                            <input type="number" min={1} max={100} value={rThresh}
                                onChange={e => setRThresh(Number(e.target.value))} className={inputClass} />
                        </Field>
                        <Field label="Heartbeat (s)">
                            <input type="number" min={0.1} max={30} step={0.1} value={heartbeat}
                                onChange={e => setHeartbeat(Number(e.target.value))} className={inputClass} />
                        </Field>
                    </div>
                    <button
                        onClick={() => onApply({
                            heartbeat_interval_s: heartbeat,
                            f_thresh: fThresh, n: nThresh, r_thresh: rThresh,
                        })}
                        disabled={busy !== null}
                        className={`${primaryBtn} mt-2 w-full`}
                    >
                        {state.running ? 'Re-apply to Lab' : 'Apply to Lab'}
                    </button>
                    <button onClick={onTest} disabled={busy !== null || !state.running}
                        className={`${ghostBtn} mt-2 w-full`}>
                        Send test packet
                    </button>
                </section>

                <NodeRoster state={state} onPatch={onPatch} />
            </div>
        </section>
    );
}

function NodeRoster({
    state, onPatch,
}: {
    state: PlaygroundState;
    onPatch: LeftPanelProps['onPatch'];
}) {
    return (
        <section>
            <SubLabel>Nodes ({state.topology.nodes.length}/{state.max_nodes})</SubLabel>
            <ul className="space-y-1 max-h-56 overflow-y-auto pr-1">
                {state.nodes.map(node => (
                    <li key={node.node_id}
                        className="flex items-center gap-2 text-[12px] rounded border border-white/5 px-2 py-1.5">
                        <span className="w-2 h-2 rounded-full shrink-0"
                            style={{ background: stateTone(node) }} />
                        <span className="text-white/70 w-8 shrink-0">{node.node_id}</span>
                        <span className="text-white/60 truncate flex-1">
                            {node.label !== node.node_id ? node.label : node.role}
                        </span>
                        <button
                            onClick={() => onPatch(
                                (nodes, links) => ({
                                    nodes: nodes.map(n => n.node_id === node.node_id
                                        ? { ...n, role: nextRole(n.role) } : n),
                                    links,
                                }),
                                state.source, state.destination,
                            )}
                            className="text-[10px] text-white/60 hover:text-white/70 shrink-0"
                            title={`Role: ${node.role} — click to change`}
                        >
                            {node.role.slice(0, 3)}
                        </button>
                        <button
                            onClick={() => {
                                if (!window.confirm(`Delete ${node.node_id} and every link touching it?`)) return;
                                onPatch(
                                    (nodes, links) => ({
                                        nodes: nodes.filter(n => n.node_id !== node.node_id),
                                        links: links.filter(l =>
                                            l.node_a !== node.node_id && l.node_b !== node.node_id),
                                    }),
                                    state.source === node.node_id ? null : state.source,
                                    state.destination === node.node_id ? null : state.destination,
                                );
                            }}
                            className="text-white/60 hover:text-red-400 shrink-0"
                            aria-label={`Delete ${node.node_id}`}
                        >
                            <Trash2 size={12} />
                        </button>
                    </li>
                ))}
                {state.nodes.length === 0 && (
                    <li className="text-[12px] text-white/60">No nodes yet.</li>
                )}
            </ul>
        </section>
    );
}

type LeftPanelProps = {
    onPatch: (
        mutate: TopologyMutator,
        source: string | null,
        destination: string | null,
    ) => Promise<PlaygroundState | null>;
};

function stateTone(node: CanvasNode) {
    if (node.failed) return '#ef4444';
    if (!node.running) return 'rgba(255,255,255,0.25)';
    return '#2dd4bf';
}

function nextRole(role: string) {
    const index = ROLES.indexOf(role as (typeof ROLES)[number]);
    return ROLES[(index + 1) % ROLES.length];
}

// ---------------------------------------------------------------------------
// Right panel — inspection and failure
// ---------------------------------------------------------------------------

function RightPanel({
    state, busy, node, link, onFailNode, onRestoreNode, onFailLink,
    onRestoreLink, onTest, onSelectLink, onHighlight,
}: {
    state: PlaygroundState;
    busy: string | null;
    node?: CanvasNode;
    link?: CanvasLink;
    onFailNode: (id: string) => void;
    onRestoreNode: (id: string) => void;
    onFailLink: (id: string) => void;
    onRestoreLink: (id: string) => void;
    onTest: () => void;
    onSelectLink: (id: string) => void;
    onHighlight: () => void;
}) {
    return (
        <section className="border border-white/10 rounded-lg bg-black/40 flex flex-col">
            <PanelBar title="Inspect" right={node ? `NODE ${node.node_id}` : link ? `LINK ${link.link_id}` : 'NOTHING SELECTED'}
                rightTone={node ? nodeTone(node) : link ? (link.enabled ? '#2dd4bf' : '#ef4444') : 'rgba(255,255,255,0.3)'} />

            <div className="p-4 space-y-4 max-h-[560px] overflow-y-auto">
                <RouteSummary state={state} />

                {node && (
                    <NodeInspector
                        node={node}
                        state={state}
                        busy={busy}
                        onFail={onFailNode}
                        onRestore={onRestoreNode}
                        onTest={onTest}
                        onSelectLink={onSelectLink}
                    />
                )}

                {link && (
                    <LinkInspector
                        link={link}
                        state={state}
                        busy={busy}
                        onFail={onFailLink}
                        onRestore={onRestoreLink}
                        onTest={onTest}
                        onHighlight={onHighlight}
                        onSelectLink={onSelectLink}
                    />
                )}

                {!node && !link && (
                    <p className="text-[12px] text-white/60 leading-relaxed">
                        Select a node or a link on the canvas to see what it is,
                        what it is connected to, and the controls that actually
                        break it.
                    </p>
                )}
            </div>
        </section>
    );
}

function RouteSummary({ state }: { state: PlaygroundState }) {
    // The route version is read from the source node's own counter, so it is the
    // version that node's forwarding table is actually on.
    const routeVersion = state.nodes.find(n => n.node_id === state.source)
        ?.counters?.route_version;
    const rows: [string, React.ReactNode][] = [
        ['Current route', state.route
            ? state.route.join(' → ')
            : <span className="text-red-400/80">UNREACHABLE</span>],
        ['Previous route', state.previous_route ? state.previous_route.join(' → ') : '—'],
        ['Route changes', String(state.route_changes)],
        ['Route version', state.running && routeVersion !== undefined ? String(routeVersion) : '—'],
        ['Packets', `${state.packets.delivered} delivered / ${state.packets.sent} sent / ${state.packets.failed} failed`],
    ];
    return (
        <section className="border border-white/10 rounded-md p-3">
            <SubLabel>Routing</SubLabel>
            <dl className="space-y-1.5 text-[12px]">
                {rows.map(([k, v]) => (
                    <div key={k} className="flex gap-2">
                        <dt className="text-white/60 w-28 shrink-0">{k}</dt>
                        <dd className="text-white/70 break-words">{v}</dd>
                    </div>
                ))}
            </dl>
        </section>
    );
}

function NodeInspector({
    node, state, busy, onFail, onRestore, onTest, onSelectLink,
}: {
    node: CanvasNode;
    state: PlaygroundState;
    busy: string | null;
    onFail: (id: string) => void;
    onRestore: (id: string) => void;
    onTest: () => void;
    onSelectLink: (id: string) => void;
}) {
    const neighbours = state.links.filter(
        l => l.node_a === node.node_id || l.node_b === node.node_id,
    );
    return (
        <section className="border border-white/10 rounded-md p-3 space-y-3">
            <div>
                <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full" style={{ background: nodeTone(node) }} />
                    <span className="text-white">{node.node_id}</span>
                    <span className="text-[11px] text-white/60">{node.label}</span>
                </div>
                <dl className="mt-2 space-y-1 text-[12px]">
                    <Row k="Role" v={node.role} />
                    <Row k="State" v={node.failed ? 'FAILED' : node.running ? 'UP' : 'IDLE'} />
                    <Row k="Listener" v={node.port ? `127.0.0.1:${node.port}` : 'not started'} />
                    {node.counters && (
                        <>
                            <Row k="Forwarded" v={String(node.counters.forwarded)} />
                            <Row k="Delivered" v={String(node.counters.delivered)} />
                            <Row k="Dropped (no route)" v={String(node.counters.dropped_no_route)} />
                        </>
                    )}
                </dl>
            </div>

            {node.failed && (
                <p className="text-[12px] text-red-300/90 leading-relaxed">
                    Its listener is closed. It cannot forward, and the routing
                    tables were rebuilt around it.
                </p>
            )}

            <div>
                <SubLabel>Neighbours</SubLabel>
                <div className="flex flex-wrap gap-1.5">
                    {neighbours.map(l => (
                        <button key={l.link_id} onClick={() => onSelectLink(l.link_id)}
                            className="px-2 py-1 rounded border border-white/10 text-[11px] text-white/55 hover:border-white/30 hover:text-white">
                            {l.link_id} {l.enabled ? '' : '· down'}
                        </button>
                    ))}
                    {neighbours.length === 0 && (
                        <span className="text-[11px] text-white/60">none</span>
                    )}
                </div>
            </div>

            <div className="flex flex-wrap gap-2">
                {node.failed ? (
                    <button onClick={() => onRestore(node.node_id)} disabled={busy !== null}
                        className={`${primaryBtn}`}>Restore node</button>
                ) : (
                    <button onClick={() => onFail(node.node_id)} disabled={busy !== null || !state.running}
                        className="h-8 px-3 rounded border border-red-500/40 text-[12px] text-red-300 hover:bg-red-950/30 disabled:opacity-30 transition-colors">
                        Fail node
                    </button>
                )}
                <button onClick={onTest} disabled={busy !== null || !state.running}
                    className={ghostBtn}>Test connectivity</button>
            </div>
        </section>
    );
}

function LinkInspector({
    link, state, busy, onFail, onRestore, onTest, onHighlight, onSelectLink,
}: {
    link: CanvasLink;
    state: PlaygroundState;
    busy: string | null;
    onFail: (id: string) => void;
    onRestore: (id: string) => void;
    onTest: () => void;
    onHighlight: () => void;
    onSelectLink: (id: string) => void;
}) {
    const a = state.nodes.find(n => n.node_id === link.node_a);
    const b = state.nodes.find(n => n.node_id === link.node_b);

    // If this link carried the current route, what would remain without it?
    const routeImpact = routeImpactOf(state, link.link_id);

    return (
        <section className="border border-white/10 rounded-md p-3 space-y-3">
            <div>
                <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full" style={{ background: link.enabled ? '#2dd4bf' : '#ef4444' }} />
                    <span className="text-white">{link.link_id}</span>
                </div>
                <dl className="mt-2 space-y-1 text-[12px]">
                    <Row k="Endpoints" v={`${link.node_a} ↔ ${link.node_b}`} />
                    <Row k="Status" v={link.status === 'UP' ? 'UP' : 'DOWN'} />
                    <Row k="Enabled" v={link.enabled ? 'yes' : 'no'} />
                    <Row k="On active route" v={link.in_route ? 'yes' : 'no'} />
                    <Row k="Detector at {a?.node_id}" v={link.state_a} />
                    <Row k="Detector at {b?.node_id}" v={link.state_b} />
                </dl>
            </div>

            {link.issues.length > 0 && (
                <div className="border border-red-500/30 bg-red-950/15 rounded p-2.5">
                    <SubLabel>Findings</SubLabel>
                    <ul className="space-y-1.5">
                        {link.issues.map((issue, i) => (
                            <li key={i} className="text-[12px] leading-relaxed" style={{ color: issueTone(issue.severity) }}>
                                {issue.message}
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            {link.issues.length === 0 && link.notes.length > 0 && (
                <div className="border border-white/10 rounded p-2.5">
                    <SubLabel>Notes</SubLabel>
                    <ul className="space-y-1">
                        {link.notes.map((note, i) => (
                            <li key={i} className="text-[12px] text-white/45 leading-relaxed">{note.message}</li>
                        ))}
                    </ul>
                </div>
            )}

            <div className="border border-white/10 rounded p-2.5">
                <SubLabel>Route impact</SubLabel>
                {routeImpact.onRoute ? (
                    routeImpact.alternate ? (
                        <p className="text-[12px] text-white/55 leading-relaxed">
                            This link carries the active route. Failing it moves
                            traffic to <strong className="text-white/80">
                                {routeImpact.alternate.join(' → ')}
                            </strong>.
                        </p>
                    ) : (
                        <p className="text-[12px] text-red-300/90 leading-relaxed">
                            This link carries the only route to the destination.
                            Failing it makes the destination unreachable.
                        </p>
                    )
                ) : (
                    <p className="text-[12px] text-white/45 leading-relaxed">
                        Not on the active route. Failing it does not change the
                        current path.
                    </p>
                )}
            </div>

            <div className="flex flex-wrap gap-2">
                {link.enabled ? (
                    <button onClick={() => onFail(link.link_id)} disabled={busy !== null || !state.running}
                        className="h-8 px-3 rounded border border-red-500/40 text-[12px] text-red-300 hover:bg-red-950/30 disabled:opacity-30 transition-colors">
                        Disable link
                    </button>
                ) : (
                    <button onClick={() => onRestore(link.link_id)} disabled={busy !== null || !state.running}
                        className={primaryBtn}>Restore link</button>
                )}
                <button onClick={onTest} disabled={busy !== null || !state.running} className={ghostBtn}>
                    Test link
                </button>
                <button onClick={onHighlight} className={ghostBtn}>Highlight problem</button>
            </div>
        </section>
    );
}

function issueTone(severity?: string) {
    if (severity === 'failed') return '#fca5a5';
    if (severity === 'suspect') return '#fcd34d';
    return 'rgba(255,255,255,0.6)';
}

function routeImpactOf(state: PlaygroundState, linkId: string) {
    const onRoute = Boolean(state.route && routeLinkIds(state.route).includes(linkId));
    if (!onRoute || !state.validation.alternate_path) return { onRoute, alternate: null };
    return { onRoute, alternate: state.validation.alternate_path };
}

function routeLinkIds(path: string[]) {
    const out: string[] = [];
    for (let i = 0; i < path.length - 1; i += 1) {
        out.push([path[i], path[i + 1]].sort().join('-'));
    }
    return out;
}

// ---------------------------------------------------------------------------
// Bottom panels
// ---------------------------------------------------------------------------

function EventTimeline({
    events, onClear, onExport,
}: {
    events: ExperimentEvent[];
    onClear: () => void;
    onExport: (fmt: 'json' | 'csv') => Promise<void>;
}) {
    const [fmt, setFmt] = useState<'json' | 'csv'>('json');
    return (
        <section className="border border-white/10 rounded-lg bg-black/40 flex flex-col">
            <PanelBar
                title="Event timeline"
                right={`${events.length} events`}
                rightTone="rgba(255,255,255,0.3)"
            />
            <div className="px-4 pt-3 flex gap-2">
                <button onClick={onClear} className={`${ghostBtn} px-3`}>Clear events</button>
                <select value={fmt} onChange={e => setFmt(e.target.value as 'json' | 'csv')}
                    className="bg-transparent border border-white/15 rounded px-2 text-[12px] text-white/60">
                    <option value="json">JSON</option>
                    <option value="csv">CSV</option>
                </select>
                <button onClick={() => onExport(fmt)} className={`${ghostBtn} px-3`}>
                    Export experiment log
                </button>
            </div>
            <ol className="mt-3 px-4 pb-4 max-h-72 overflow-y-auto font-mono text-[11.5px] space-y-1">
                {[...events].reverse().map(event => (
                    <li key={event.seq} className="flex gap-2 border-b border-white/5 pb-1">
                        <span className="text-white/60 shrink-0 tabular-nums">
                            {new Date(event.ts * 1000).toLocaleTimeString()}
                        </span>
                        <span className="text-white/60 shrink-0 w-16 uppercase truncate">
                            {event.category}
                        </span>
                        <span className={SEVERITY_TONE[event.severity] ?? 'text-white/55'}>
                            {event.message}
                        </span>
                    </li>
                ))}
                {events.length === 0 && (
                    <li className="text-white/60">Nothing has happened yet.</li>
                )}
            </ol>
        </section>
    );
}

function ConnectivityPanel({ state }: { state: PlaygroundState }) {
    const delivery = state.last_delivery;
    return (
        <section className="border border-white/10 rounded-lg bg-black/40 flex flex-col">
            <PanelBar title="Connectivity" right={
                delivery ? (delivery.delivered ? 'DELIVERED' : 'NOT DELIVERED') : 'NO TEST YET'
            } rightTone={delivery ? (delivery.delivered ? '#2dd4bf' : '#ef4444') : 'rgba(255,255,255,0.3)'} />

            <div className="p-4 space-y-3 text-[12px]">
                {delivery && (
                    <div className={`border rounded p-3 ${delivery.delivered
                        ? 'border-teal-500/30 bg-teal-950/10'
                        : 'border-red-500/30 bg-red-950/10'}`}>
                        <div className="flex items-center gap-2">
                            {delivery.delivered
                                ? <CheckCircle2 size={14} className="text-teal-300" />
                                : <AlertTriangle size={14} className="text-red-300" />}
                            <span className={delivery.delivered ? 'text-teal-200' : 'text-red-200'}>
                                {delivery.delivered ? 'Delivered' : 'Not delivered'}
                            </span>
                        </div>
                        <p className="mt-1.5 text-white/60">
                            {delivery.source} → {delivery.destination}
                        </p>
                        {delivery.path && delivery.delivered && (
                            <p className="mt-1 text-white/80 font-mono">
                                {delivery.path.join(' → ')}
                            </p>
                        )}
                        {!delivery.delivered && delivery.reason && (
                            <p className="mt-1 text-red-200/80">{delivery.reason}</p>
                        )}
                    </div>
                )}

                <dl className="space-y-1.5">
                    <Row k="Network" v={state.running ? 'running' : 'not running'} />
                    <Row k="Topology" v={state.running
                        ? (state.draft_is_running ? 'matches the canvas' : 'edited — re-apply')
                        : `${state.topology.nodes.length} nodes, ${state.links.length} links`} />
                    <Row k="Validation" v={state.validation.summary} />
                </dl>
            </div>
        </section>
    );
}

// ---------------------------------------------------------------------------
// Hysteresis
// ---------------------------------------------------------------------------

function HysteresisPanel({
    state, busy, onRun, onSelectLink,
}: {
    state: PlaygroundState;
    busy: string | null;
    onRun: Runner;
    onSelectLink: (id: string) => void;
}) {
    const linkIds = state.links.map(l => l.link_id);
    const [monitored, setMonitored] = useState('');
    const link = monitored || linkIds[0] || '';
    const [f, setF] = useState(3);
    const [n, setN] = useState(5);
    const [r, setR] = useState(5);
    const [useEwma, setUseEwma] = useState(false);
    const [preset, setPreset] = useState<keyof typeof SEQUENCES>('flapping');
    const [custom, setCustom] = useState('');
    const [comparison, setComparison] = useState<DetectorComparison | null>(null);
    const [observed, setObserved] = useState<string[] | null>(null);
    const stopWatch = useRef<(() => void) | null>(null);

    useEffect(() => {
        if (!monitored && linkIds.length) setMonitored(linkIds[0]);
    }, [linkIds, monitored]);

    // Seed the thresholds from what the running detector is actually set to,
    // rather than making the operator retype them.
    useEffect(() => {
        const reading = firstReading(state, link);
        if (reading) {
            setF(reading.f_thresh); setN(reading.n); setR(reading.r_thresh);
            setUseEwma(reading.use_ewma);
        }
    }, [link]); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => () => stopWatch.current?.(), []);

    const readings = link ? detectorReadings(state, link) : [];

    const configure = async () => {
        if (!link) return;
        await onRun('detector', async () => {
            const result = await playgroundApi.configureDetector({
                link_id: link, f_thresh: f, n, r_thresh: r, use_ewma: useEwma,
            });
            return result.snapshot;
        });
    };

    /**
     * The live experiment: stop the other end of the monitored link and watch
     * the detector cross its own thresholds on real missed heartbeats, then put
     * it back and watch it recover. Nothing here is simulated — the states
     * listed are read from `/api/playground/transitions` after each step.
     */
    const runLive = async () => {
        if (!link || !state.running) return;

        // Stop the end of this link that is neither the test source nor the
        // destination — a forwarding node, so taking it down really does stop
        // the heartbeats the other end measures.
        const parts = link.split('-');
        const victim = state.nodes.find(
            n => (n.node_id === parts[0] || n.node_id === parts[1])
                && n.node_id !== state.source
                && n.node_id !== state.destination
                && !n.failed && n.running,
        );
        if (!victim) {
            setComparison(null);
            setObserved([
                'No forwarding node at the far end of this link to stop. '
                + 'Pick a link whose other end is a router.',
            ]);
            return;
        }

        let cancelled = false;
        stopWatch.current = () => { cancelled = true; };

        const sequence: string[] = [];
        const note = async () => {
            if (cancelled) return;
            const seen = await playgroundApi.transitions(link);
            const latest = seen.transitions[seen.transitions.length - 1];
            if (latest && sequence[sequence.length - 1] !== latest.to_state) {
                sequence.push(latest.to_state);
            }
            return latest?.to_state;
        };

        try {
            await onRun('live', async () => {
                const opening = firstState(state, link);
                if (opening) sequence.push(opening);
                await playgroundApi.failNode(victim.node_id);

                // Watch for the crossing into FAILED — real missed heartbeats,
                // counted by the detector thresholds configured above.
                for (let i = 0; i < 80 && !cancelled; i += 1) {
                    await sleep(250);
                    if ((await note()) === 'FAILED') break;
                }

                await playgroundApi.restoreNode(victim.node_id);

                // Then watch it come back: FAILED → HEALTHY once R_thresh
                // consecutive heartbeats have landed.
                for (let i = 0; i < 80 && !cancelled; i += 1) {
                    await sleep(250);
                    if (sequence[sequence.length - 1] === 'HEALTHY') break;
                    await note();
                }
                if (cancelled) return undefined;
                return playgroundApi.state();
            });
        } finally {
            stopWatch.current = null;
        }
        setObserved(sequence);
    };

    const runComparison = async () => {
        const sequence = custom.trim()
            ? custom.split(/[,\s]+/).filter(Boolean).map(t => t === '1' || t.toLowerCase() === 'true')
            : SEQUENCES[preset].values;
        try {
            setComparison(await playgroundApi.compare({
                link_id: link || 'probe-link',
                sequence,
                f_thresh: f, n, r_thresh: r,
                use_ewma: useEwma,
                baseline_threshold: f,
            }));
        } catch {
            /* surfaced by the page-level error banner */
        }
    };

    return (
        <section className="border border-white/10 rounded-lg bg-black/40 flex flex-col">
            <PanelBar
                title="Hysteresis controls"
                right={link ? `MONITORING ${link}` : 'NO LINK SELECTED'}
                rightTone={link ? '#crimson' : 'rgba(255,255,255,0.3)'}
            />
            <div className="p-4 space-y-4 text-[12px]">
                <Field label="Monitored link">
                    <select value={link} onChange={e => { setMonitored(e.target.value); onSelectLink(e.target.value); }}
                        className={inputClass}>
                        {linkIds.map(id => <option key={id} value={id}>{id}</option>)}
                        {linkIds.length === 0 && <option value="">— none —</option>}
                    </select>
                </Field>

                {/* Live readings */}
                <div className="border border-white/10 rounded p-2.5">
                    <SubLabel>Live detector readings</SubLabel>
                    {readings.length === 0 ? (
                        <p className="text-white/60">Apply a topology to start detectors.</p>
                    ) : (
                        <table className="w-full text-[11px]">
                            <thead>
                                <tr className="text-white/60 text-left">
                                    <th className="font-normal pb-1">at</th>
                                    <th className="font-normal pb-1">state</th>
                                    <th className="font-normal pb-1 text-right">miss</th>
                                    <th className="font-normal pb-1 text-right">ok</th>
                                    <th className="font-normal pb-1 text-right">flaps</th>
                                </tr>
                            </thead>
                            <tbody>
                                {readings.map(reading => (
                                    <tr key={reading.node_id}>
                                        <td className="text-white/55">{reading.node_id}</td>
                                        <td style={{ color: DETECTOR_TONE[reading.state] }}>
                                            {reading.state}
                                        </td>
                                        <td className="text-right text-white/45 tabular-nums">
                                            {reading.consecutive_misses}
                                        </td>
                                        <td className="text-right text-white/45 tabular-nums">
                                            {reading.consecutive_successes}
                                        </td>
                                        <td className="text-right text-white/45 tabular-nums">
                                            {reading.route_flap_count}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>

                {/* Parameters */}
                <div>
                    <SubLabel>Detector parameters</SubLabel>
                    <div className="grid grid-cols-4 gap-2">
                        <Field label="F_thresh">
                            <input type="number" min={1} max={50} value={f}
                                onChange={e => setF(Number(e.target.value))} className={inputClass} />
                        </Field>
                        <Field label="N">
                            <input type="number" min={1} max={100} value={n}
                                onChange={e => setN(Number(e.target.value))} className={inputClass} />
                        </Field>
                        <Field label="R_thresh">
                            <input type="number" min={1} max={100} value={r}
                                onChange={e => setR(Number(e.target.value))} className={inputClass} />
                        </Field>
                        <Field label="ratio">
                            <input readOnly value={(r / Math.max(f, 1)).toFixed(2)} className={`${inputClass} text-white/45`} />
                        </Field>
                    </div>
                    <label className="flex items-center gap-2 mt-2 text-[11px] text-white/50">
                        <input type="checkbox" checked={useEwma} onChange={e => setUseEwma(e.target.checked)} />
                        EWMA smoothing on heartbeat quality
                    </label>
                    {r <= f && (
                        <p className="text-[11px] text-amber-300/90 mt-2 leading-relaxed">
                            R_thresh ≤ F_thresh leaves no dead band. The state machine
                            itself warns that route flapping is likely at this ratio.
                        </p>
                    )}
                    <div className="flex gap-2 mt-2">
                        <button onClick={configure} disabled={busy !== null || !link || !state.running}
                            className={`${ghostBtn} flex-1`}>Apply to live detector</button>
                        <button onClick={runLive} disabled={busy !== null || !link || !state.running}
                            className={`${ghostBtn} flex-1`} title="Stop the other end of this link and watch the detector cross its thresholds">
                            Run live experiment
                        </button>
                    </div>
                </div>

                {/* Observed states */}
                {observed && (
                    <div className="border border-white/10 rounded p-2.5">
                        <SubLabel>States observed</SubLabel>
                        <div className="flex flex-wrap items-center gap-1.5">
                            {observed.map((s, i) => (
                                <React.Fragment key={`${s}-${i}`}>
                                    {i > 0 && <span className="text-white/60">→</span>}
                                    <span style={{ color: DETECTOR_TONE[s] ?? '#fff' }}>{s}</span>
                                </React.Fragment>
                            ))}
                        </div>
                        <p className="text-[11px] text-white/60 mt-2 leading-relaxed">
                            Read from the node's own transition record after each
                            step. Recovery here is a direct FAILED → HEALTHY
                            transition — this detector has no RECOVERING state.
                        </p>
                    </div>
                )}

                {/* Comparison */}
                <div className="border-t border-white/10 pt-3">
                    <SubLabel>Single threshold vs hysteresis</SubLabel>
                    <Field label="Input sequence">
                        <select value={preset} onChange={e => setPreset(e.target.value as keyof typeof SEQUENCES)}
                            className={inputClass}>
                            {Object.entries(SEQUENCES).map(([key, s]) => (
                                <option key={key} value={key}>{s.label}</option>
                            ))}
                        </select>
                    </Field>
                    {SEQUENCES[preset].hint && (
                        <p className="text-[11px] text-white/60 mt-1 mb-2">
                            {SEQUENCES[preset].hint}
                        </p>
                    )}
                    <input
                        value={custom}
                        onChange={e => setCustom(e.target.value)}
                        placeholder="Custom: 1,1,1,0,0,1,1 … (1 = heartbeat arrived)"
                        className={`${inputClass} mt-1`}
                    />
                    <button onClick={runComparison} disabled={busy !== null || (!link && !custom)}
                        className={`${primaryBtn} mt-2 w-full`}>
                        <Radio size={12} className="mr-1.5 inline" /> Run comparison
                    </button>

                    {comparison && (
                        <div className="mt-3 border border-white/10 rounded p-2.5 space-y-2">
                            <ComparisonRow
                                name="Hysteresis"
                                transitions={comparison.hysteresis.transitions}
                                flaps={comparison.hysteresis.route_flap_count}
                            />
                            <ComparisonRow
                                name="Single threshold"
                                detail={`threshold ${comparison.naive.threshold}`}
                                transitions={comparison.naive.transitions}
                                flaps={comparison.naive.route_flap_count}
                            />
                            <div className="border-t border-white/10 pt-2 text-[11px] text-white/60">
                                {comparison.route_flaps_saved > 0
                                    ? `Hysteresis changed the usable path ${comparison.route_flaps_saved} fewer time(s) over ${comparison.sequence.length} heartbeats.`
                                    : comparison.route_flaps_saved === 0
                                        ? `Both detectors changed the usable path ${comparison.hysteresis.route_flap_count} time(s) over ${comparison.sequence.length} heartbeats.`
                                        : `The single-threshold detector used ${-comparison.route_flaps_saved} fewer change(s) on this sequence — widen R_thresh to grow the dead band.`}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </section>
    );
}

function ComparisonRow({
    name, detail, transitions, flaps,
}: {
    name: string;
    detail?: string;
    transitions: { from: string; to: string }[];
    flaps: number;
}) {
    return (
        <div>
            <div className="flex items-baseline justify-between">
                <span className="text-white/75">{name}</span>
                <span className="text-white/50 tabular-nums">
                    {flaps} route change{flaps === 1 ? '' : 's'}
                </span>
            </div>
            {detail && <div className="text-[10.5px] text-white/60">{detail}</div>}
            <div className="flex flex-wrap items-center gap-1 mt-1">
                {transitions.length === 0 && <span className="text-white/60">no transitions</span>}
                {transitions.map((t, i) => (
                    <React.Fragment key={i}>
                        {i > 0 && <span className="text-white/60">→</span>}
                        <span style={{ color: DETECTOR_TONE[t.to] ?? '#fff' }}>{t.to}</span>
                    </React.Fragment>
                ))}
            </div>
        </div>
    );
}

function firstReading(state: PlaygroundState, linkId: string) {
    return detectorReadings(state, linkId)[0];
}

function detectorReadings(state: PlaygroundState, linkId: string) {
    const parts = linkId.split('-');
    const out: (import('../playground/types').DetectorReading & { node_id: string })[] = [];
    for (const [nodeId, neighbours] of Object.entries(state.detectors)) {
        for (const neighbourId of [parts[0], parts[1]]) {
            const reading = neighbours[neighbourId];
            if (reading && reading.link_id === linkId && !out.some(o => o.node_id === nodeId)) {
                out.push({ ...reading, node_id: nodeId });
            }
        }
    }
    return out;
}

function firstState(state: PlaygroundState, linkId: string) {
    return detectorReadings(state, linkId)[0]?.state;
}

// ---------------------------------------------------------------------------
// Small parts
// ---------------------------------------------------------------------------

function PanelBar({
    title, right, rightTone,
}: {
    title: string;
    right?: string;
    rightTone?: string;
}) {
    return (
        <div className="flex items-center justify-between px-4 h-11 border-b border-white/10 shrink-0">
            <h2 className="text-[12px] uppercase tracking-[0.14em] text-white/60">{title}</h2>
            {right && (
                <span className="text-[11px] font-mono" style={{ color: rightTone ?? 'rgba(255,255,255,0.4)' }}>
                    {right}
                </span>
            )}
        </div>
    );
}

function SubLabel({ children }: { children: React.ReactNode }) {
    return (
        <h3 className="text-[10.5px] uppercase tracking-[0.14em] text-white/60 mb-1.5">
            {children}
        </h3>
    );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <label className="block">
            <span className="block text-[10.5px] text-white/60 mb-1">{label}</span>
            {children}
        </label>
    );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
    return (
        <div className="flex gap-2">
            <dt className="text-white/60 w-28 shrink-0">{k}</dt>
            <dd className="text-white/70 break-words">{v}</dd>
        </div>
    );
}

function ValidationBar({ state }: { state: PlaygroundState }) {
    const v = state.validation;
    const tone = !v.valid ? '#ef4444' : !v.routable ? '#ef4444' : v.has_alternate ? '#2dd4bf' : '#f5b54a';
    const label = !v.valid ? 'INVALID'
        : !v.routable ? 'NO ROUTE'
            : v.has_alternate ? 'ROUTABLE · ALTERNATE EXISTS'
                : 'ROUTABLE · SINGLE PATH';
    return (
        <div className="border-t border-white/10 px-4 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px]">
            <span style={{ color: tone }}>{label}</span>
            <span className="text-white/40">{v.summary}</span>
            {v.errors.slice(0, 2).map((e, i) => (
                <span key={i} className="text-red-300/80">• {e.message}</span>
            ))}
        </div>
    );
}

const inputClass =
    'w-full bg-transparent border border-white/15 rounded px-2 py-1.5 text-[12px] ' +
    'text-white placeholder-white/25 focus:outline-none focus:border-white/40';

const primaryBtn =
    'h-8 px-3 rounded bg-white text-black text-[12px] font-medium ' +
    'hover:bg-white/90 disabled:opacity-30 transition-colors';

const ghostBtn =
    'h-8 px-3 rounded border border-white/15 text-[12px] text-white/70 ' +
    'hover:border-white/40 hover:text-white disabled:opacity-30 transition-colors';

function clamp(value: number, min: number, max: number) {
    return Math.min(max, Math.max(min, value));
}

function sleep(ms: number) {
    return new Promise(resolve => window.setTimeout(resolve, ms));
}

function message(exc: unknown) {
    if (exc instanceof ApiError) return exc.message;
    if (exc instanceof Error) return exc.message;
    return 'Something went wrong.';
}

function nodeTone(node: CanvasNode) {
    if (node.failed) return '#ef4444';
    if (!node.running) return 'rgba(255,255,255,0.28)';
    if (node.detector && Object.values(node.detector).some(r => r.state === 'SUSPECT')) return '#f5b54a';
    return '#2dd4bf';
}