/**
 * DiagnosticsLab.tsx — Feature 3's dashboard: eight sections, each labelled.
 *
 * The lab is deliberately not one giant "score". It shows:
 *
 *   1  Live Network Status      real adapters, IP configuration, gateway
 *   2  Latency & Packet Loss    real ping statistics + simulated fault timeline
 *   3  Route Trace              real tracert hops
 *   4  Network Adapters         real interface list
 *   5  Benchmark History        stored runs, comparison, exports
 *   6  Hysteresis Sweep         controlled ratio sweep + trade-off chart
 *   7  Scheduler Comparison     controlled variant comparison
 *   8  Export Reports           JSON / CSV export of a real run
 *
 * Every number carries a badge: REAL, CONTROLLED, SIMULATED or UNAVAILABLE.
 * The parent owns the one-click run (the white button in the page header) and
 * passes its result in; the interactive tools below own their own state.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import LocalOnlyNote from '../components/LocalOnlyNote';
import { IS_REMOTE_API } from '../api/client';

/** Why the real-probe buttons are disabled against a hosted API. */
export const LIVE_PROBE_BLOCKED =
    'Real probes would run on the hosting server, not on your device. Run NetRescue locally to use this.';
import {
    diagnosticsApi,
    downloadText,
    ApiError,
} from './api';
import type {
    DataKind,
    DiagnosticsRun,
    DiagnosticsStatus,
    DnsResult,
    FaultScenarioResult,
    HysteresisSweep,
    ParetoPoint,
    RunComparison,
    RunSummary,
    ScenarioDescriptor,
    SchedulerComparison,
    SweepPoint,
} from './types';

// ---------------------------------------------------------------------------
// Small shared pieces
// ---------------------------------------------------------------------------

const KIND_STYLE: Record<DataKind, string> = {
    real: 'border-emerald-400/40 text-emerald-300 bg-emerald-400/10',
    controlled: 'border-sky-400/40 text-sky-300 bg-sky-400/10',
    simulated: 'border-amber-400/40 text-amber-300 bg-amber-400/10',
    unsupported: 'border-white/20 text-white/50 bg-white/5',
};

function Badge({ kind, title }: { kind: DataKind; title?: string }) {
    return (
        <span
            title={title}
            className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em] ${KIND_STYLE[kind]}`}
        >
            {kind}
        </span>
    );
}

function Card({
    id,
    title,
    kind,
    subtitle,
    right,
    children,
}: {
    id: string;
    title: string;
    kind?: DataKind;
    subtitle?: string;
    right?: React.ReactNode;
    children: React.ReactNode;
}) {
    return (
        <section id={id} className="scroll-mt-24 border border-white/10 rounded-xl bg-white/[0.02] p-5 sm:p-6">
            <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
                <div>
                    <div className="flex items-center gap-2">
                        <h3 className="text-white text-[16px] sm:text-[18px] font-normal">{title}</h3>
                        {kind && <Badge kind={kind} />}
                    </div>
                    {subtitle && <p className="text-white/40 text-[12px] mt-1 max-w-2xl">{subtitle}</p>}
                </div>
                {right}
            </div>
            {children}
        </section>
    );
}

function Empty({ children }: { children: React.ReactNode }) {
    return <p className="text-white/40 text-[13px] leading-relaxed">{children}</p>;
}

function ErrorNote({ message }: { message: string }) {
    return (
        <p role="alert" className="text-red-300/90 text-[13px] leading-relaxed border border-red-400/30 bg-red-400/5 rounded-lg px-3 py-2">
            {message}
        </p>
    );
}

const errText = (e: unknown): string =>
    e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e);

const ms = (v: number | null | undefined, digits = 2): string =>
    v === null || v === undefined ? '—' : `${v.toFixed(digits)} ms`;

const pct = (v: number | null | undefined): string =>
    v === null || v === undefined ? '—' : `${(v * 100).toFixed(2)}%`;

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
    return (
        <div className="border border-white/10 rounded-lg px-3 py-2" title={hint}>
            <div className="text-white/40 text-[11px] uppercase tracking-[0.08em]">{label}</div>
            <div className="text-white text-[18px] font-light tabular-nums mt-0.5">{value}</div>
        </div>
    );
}

/** A small, labelled bar chart for a probe sample series. */
function LatencyBars({ samples }: { samples: number[] }) {
    if (!samples.length) return <Empty>No individual probe samples were recorded.</Empty>;
    const max = Math.max(...samples, 0.001);
    const summary = `Round-trip time per probe, milliseconds: ${samples
        .map((s) => s.toFixed(2))
        .join(', ')}.`;
    return (
        <div role="img" aria-label={summary} className="flex items-end gap-1 h-24 mt-3">
            {samples.map((s, i) => (
                <div key={i} className="flex-1 flex flex-col items-center justify-end h-full">
                    <span className="text-white/40 text-[10px] tabular-nums mb-1">{s.toFixed(1)}</span>
                    <div
                        className="w-full rounded-t bg-sky-400/50"
                        style={{ height: `${Math.max(2, (s / max) * 100)}%` }}
                        title={`Probe ${i + 1}: ${s.toFixed(2)} ms`}
                    />
                </div>
            ))}
        </div>
    );
}

/** Scatter of the hysteresis trade-off: stability (flap count) vs recovery time. */
function SweepChart({ points, frontier }: { points: SweepPoint[]; frontier: ParetoPoint[] }) {
    const plotted = points.filter((p) => p.recovery_s !== null);
    if (plotted.length < 2) {
        return <Empty>Not enough completed sweep points to plot a trade-off.</Empty>;
    }
    const W = 560;
    const H = 200;
    const padL = 46;
    const padB = 34;
    const padT = 12;
    const padR = 16;
    const xs = plotted.map((p) => p.recovery_s as number);
    const ys = plotted.map((p) => p.flap_count);
    const xMin = Math.min(...xs, 0);
    const xMax = Math.max(...xs, 1);
    const yMax = Math.max(...ys, 1);
    const px = (v: number) => padL + ((v - xMin) / (xMax - xMin || 1)) * (W - padL - padR);
    const py = (v: number) => H - padB - (v / (yMax || 1)) * (H - padT - padB);
    const ordered = [...plotted].sort((a, b) => a.ratio - b.ratio);
    const line = ordered.map((p) => `${px(p.recovery_s as number)},${py(p.flap_count)}`).join(' ');
    const frontierSet = new Set(frontier.map((f) => `${f.ratio}`));

    return (
        <div className="mt-2">
            <svg
                viewBox={`0 0 ${W} ${H}`}
                className="w-full h-auto"
                role="img"
                aria-label="Hysteresis sweep trade-off: recovery time in seconds against route flap count, one point per hysteresis ratio."
            >
                <line x1={padL} y1={H - padB} x2={W - padR} y2={H - padB} stroke="rgba(255,255,255,0.2)" />
                <line x1={padL} y1={padT} x2={padL} y2={H - padB} stroke="rgba(255,255,255,0.2)" />
                <text x={padL - 6} y={padT + 8} textAnchor="end" fill="rgba(255,255,255,0.4)" fontSize="10">
                    {yMax}
                </text>
                <text x={padL - 6} y={H - padB} textAnchor="end" fill="rgba(255,255,255,0.4)" fontSize="10">
                    0
                </text>
                <text x={(W + padL) / 2} y={H - 6} textAnchor="middle" fill="rgba(255,255,255,0.45)" fontSize="10">
                    recovery time (s) →
                </text>
                <text x={12} y={padT + 8} fill="rgba(255,255,255,0.45)" fontSize="10">
                    flaps
                </text>
                <polyline points={line} fill="none" stroke="rgba(125,211,252,0.5)" strokeWidth="1.5" />
                {ordered.map((p) => (
                    <g key={p.ratio}>
                        <circle
                            cx={px(p.recovery_s as number)}
                            cy={py(p.flap_count)}
                            r={frontierSet.has(`${p.ratio}`) ? 5 : 4}
                            fill={frontierSet.has(`${p.ratio}`) ? '#38bdf8' : 'rgba(255,255,255,0.55)'}
                            stroke="#0b0b0b"
                            strokeWidth="1"
                        >
                            <title>{`ratio ${p.ratio}: ${p.flap_count} flaps, ${(p.recovery_s as number).toFixed(2)} s recovery`}</title>
                        </circle>
                        <text
                            x={px(p.recovery_s as number)}
                            y={py(p.flap_count) - 8}
                            textAnchor="middle"
                            fill="rgba(255,255,255,0.7)"
                            fontSize="10"
                        >
                            {p.ratio}
                        </text>
                    </g>
                ))}
            </svg>
            <p className="text-white/60 text-[11px] mt-1">
                Highlighted points are the non-dominated set: lower flaps and lower recovery time is better on both axes.
            </p>
        </div>
    );
}

// ---------------------------------------------------------------------------
// The lab
// ---------------------------------------------------------------------------

export interface DiagnosticsLabProps {
    run: DiagnosticsRun | null;
    loading: boolean;
    error: string | null;
    onRerun: () => void;
}

export default function DiagnosticsLab({ run, loading, error, onRerun }: DiagnosticsLabProps) {
    const [status, setStatus] = useState<DiagnosticsStatus | null>(null);
    const [statusError, setStatusError] = useState<string | null>(null);
    const [scenarios, setScenarios] = useState<ScenarioDescriptor[]>([]);

    const [history, setHistory] = useState<RunSummary[]>([]);
    const [historyNote, setHistoryNote] = useState('');
    const [selected, setSelected] = useState<string[]>([]);
    const [comparison, setComparison] = useState<RunComparison | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);

    const [benchTarget, setBenchTarget] = useState('127.0.0.1');
    const [benchCount, setBenchCount] = useState(4);
    const [benchReps, setBenchReps] = useState(3);
    const [benchmarkRun, setBenchmarkRun] = useState<DiagnosticsRun | null>(null);
    const [benchBusy, setBenchBusy] = useState(false);

    const [faultScenario, setFaultScenario] = useState('packet_loss');
    const [faultSeed, setFaultSeed] = useState(20260101);
    const [fault, setFault] = useState<FaultScenarioResult | null>(null);
    const [faultBusy, setFaultBusy] = useState(false);

    const [sweep, setSweep] = useState<HysteresisSweep | null>(null);
    const [sweepBusy, setSweepBusy] = useState(false);

    const [scheduler, setScheduler] = useState<SchedulerComparison | null>(null);
    const [schedulerBusy, setSchedulerBusy] = useState(false);

    const [dnsName, setDnsName] = useState('');
    const [dnsResult, setDnsResult] = useState<DnsResult | null>(null);
    const [dnsBusy, setDnsBusy] = useState(false);

    const refreshHistory = useCallback(async () => {
        try {
            const data = await diagnosticsApi.history(50);
            setHistory(data.runs);
            setHistoryNote(data.note);
        } catch (e) {
            setActionError(errText(e));
        }
    }, []);

    useEffect(() => {
        let live = true;
        (async () => {
            try {
                const [s, sc] = await Promise.all([diagnosticsApi.status(), diagnosticsApi.scenarios()]);
                if (!live) return;
                setStatus(s);
                setScenarios(sc.scenarios);
                setBenchTarget(s.suggested_target || '127.0.0.1');
            } catch (e) {
                if (live) setStatusError(errText(e));
            }
        })();
        void refreshHistory();
        return () => {
            live = false;
        };
    }, [refreshHistory]);

    // Refresh history whenever a new run lands from the page header button.
    useEffect(() => {
        if (run) void refreshHistory();
    }, [run, refreshHistory]);

    const ping = run?.sections.ping?.data ?? null;
    const trace = run?.sections.traceroute?.data ?? null;
    const network = run?.sections.network_status?.data ?? null;
    const dns = run?.sections.dns?.data ?? null;

    const runBenchmark = useCallback(async () => {
        setBenchBusy(true);
        setActionError(null);
        try {
            const result = await diagnosticsApi.benchmark({
                target: benchTarget,
                count: benchCount,
                timeout_s: 2,
                repetitions: benchReps,
            });
            setBenchmarkRun(result);
            await refreshHistory();
        } catch (e) {
            setActionError(errText(e));
        } finally {
            setBenchBusy(false);
        }
    }, [benchTarget, benchCount, benchReps, refreshHistory]);

    const runFault = useCallback(async () => {
        setFaultBusy(true);
        setActionError(null);
        try {
            const result = await diagnosticsApi.faults({
                scenario: faultScenario,
                seed: faultSeed,
                count: 5,
                repetitions: 3,
            });
            setFault(result);
            await refreshHistory();
        } catch (e) {
            setActionError(errText(e));
        } finally {
            setFaultBusy(false);
        }
    }, [faultScenario, faultSeed, refreshHistory]);

    const runSweep = useCallback(async () => {
        setSweepBusy(true);
        setActionError(null);
        try {
            setSweep(await diagnosticsApi.hysteresisSweep({ repetitions: 5, total_intervals: 400 }));
        } catch (e) {
            setActionError(errText(e));
        } finally {
            setSweepBusy(false);
        }
    }, []);

    const runScheduler = useCallback(async () => {
        setSchedulerBusy(true);
        setActionError(null);
        try {
            setScheduler(await diagnosticsApi.schedulerComparison({
                // The comparison runs modelled path delays in real time. The default
                // 256 kB payload takes minutes, past a hosted proxy's ~100 s limit.
                repetitions: 5,
                ...(IS_REMOTE_API ? { payload_bytes: 32_000 } : {}),
            }));
        } catch (e) {
            setActionError(errText(e));
        } finally {
            setSchedulerBusy(false);
        }
    }, []);

    const compareSelected = useCallback(async () => {
        setActionError(null);
        try {
            setComparison(await diagnosticsApi.compare(selected));
        } catch (e) {
            setActionError(errText(e));
        }
    }, [selected]);

    const clearHistory = useCallback(async () => {
        setActionError(null);
        try {
            await diagnosticsApi.clearHistory();
            setSelected([]);
            setComparison(null);
            await refreshHistory();
        } catch (e) {
            setActionError(errText(e));
        }
    }, [refreshHistory]);

    const resolveDns = useCallback(async () => {
        const name = dnsName.trim();
        if (!name) return;
        setDnsBusy(true);
        setActionError(null);
        try {
            setDnsResult(await diagnosticsApi.dns({ name }));
        } catch (e) {
            setActionError(errText(e));
        } finally {
            setDnsBusy(false);
        }
    }, [dnsName]);

    const exportRun = useCallback(async (id: string, format: 'json' | 'csv') => {
        setActionError(null);
        try {
            const file = await diagnosticsApi.exportRun(id, format);
            downloadText(file.filename, file.content, format === 'csv' ? 'text/csv' : 'application/json');
        } catch (e) {
            setActionError(errText(e));
        }
    }, []);

    const toggleSelected = (id: string) =>
        setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id].slice(-10)));

    const latestRunId = run?.run_id ?? benchmarkRun?.run_id ?? null;
    const unsupported = status?.unsupported ?? {};
    const controlled = status?.controlled_simulation;
    const historyEmpty = history.length === 0;

    const realCaps = useMemo(
        () => Object.entries(status?.real_measurements ?? {}),
        [status],
    );

    return (
        <div className="space-y-6">
            {/* Provenance legend */}
            <div className="border border-white/10 rounded-xl bg-white/[0.02] p-4">
                <div className="flex flex-wrap items-center gap-2 text-[12px] text-white/50">
                    <span className="text-white/70">Provenance:</span>
                    <Badge kind="real" title="Measured on this machine's network just now." />
                    <span>measured now</span>
                    <Badge kind="controlled" title="A benchmark over modelled, declarative conditions." />
                    <span>modelled conditions</span>
                    <Badge kind="simulated" title="Produced by the seeded fault simulator." />
                    <span>seeded simulator</span>
                    <Badge kind="unsupported" title="This host cannot provide the capability; the reason is shown." />
                    <span>unavailable here</span>
                </div>
            </div>

            <LocalOnlyNote>
                Live probes (adapters, ping, traceroute, DNS, benchmarks) would run on the Render server, not on your
                network, and are disabled for public visitors. The seeded fault scenarios, hysteresis sweep and
                scheduler comparison are simulations and work here. Run NetRescue locally for real measurements.
            </LocalOnlyNote>

            {(error || actionError || statusError) && (
                <ErrorNote message={error || actionError || statusError || ''} />
            )}

            {/* 1 — Live Network Status */}
            <Card
                id="diag-live-status"
                title="1 · Live Network Status"
                kind="real"
                right={
                    <button
                        onClick={onRerun}
                        disabled={loading || IS_REMOTE_API}
                        title={IS_REMOTE_API ? LIVE_PROBE_BLOCKED : undefined}
                        className="h-9 px-4 bg-white text-black rounded-full text-[13px] font-medium disabled:opacity-50 hover:bg-white/90 transition-colors"
                    >
                        {loading ? 'Running…' : 'Run Diagnostics'}
                    </button>
                }
            >
                {run?.sections.network_status?.status === 'skipped' && (
                    <Empty>Adapter and IP sections were disabled for this run.</Empty>
                )}
                {run?.sections.network_status?.status === 'error' && (
                    <ErrorNote message={run.sections.network_status.error || 'Adapter query failed.'} />
                )}
                {loading && !run && <Empty>Running real probes…</Empty>}

                {network && (
                    <div className="space-y-4">
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                            <Metric label="Default gateway" value={network.ip_configuration?.default_gateway ?? '—'} />
                            <Metric label="Adapters up" value={
                                network.adapters?.available
                                    ? `${network.adapters.up_count ?? 0} / ${network.adapters.count ?? 0}`
                                    : '—'
                            } />
                            <Metric label="Host" value={network.hostname ?? '—'} />
                            <Metric label="Platform" value={`${network.platform ?? '—'} ${network.release ?? ''}`.trim()} />
                        </div>
                        <div className="flex flex-wrap gap-2 text-[11px] text-white/60">
                            {run?.config_hash && <span>config {run.config_hash.slice(0, 12)}…</span>}
                            {run?.software_version && <span>v{run.software_version}</span>}
                            {network.adapters?.cached && <span>adapters cached ≤ {network.adapters.cache_ttl_s}s</span>}
                            {run?.persisted === false && <span>history: in-process only (not persisted)</span>}
                        </div>
                    </div>
                )}

                {!network && run && run.sections.network_status?.data === undefined && (
                    <Empty>The network status section returned no data for this run.</Empty>
                )}

                <div className="mt-5 pt-4 border-t border-white/10">
                    <div className="flex flex-wrap items-end gap-3">
                        <label className="text-[12px] text-white/50">
                            Resolve name
                            <input
                                value={dnsName}
                                onChange={(e) => setDnsName(e.target.value)}
                                placeholder="example.com"
                                aria-label="DNS name to resolve"
                                className="ml-2 bg-black/40 border border-white/15 rounded-md px-2 py-1 text-white text-[13px] w-52"
                            />
                        </label>
                        <button
                            onClick={resolveDns}
                            disabled={dnsBusy || !dnsName.trim() || IS_REMOTE_API}
                            title={IS_REMOTE_API ? LIVE_PROBE_BLOCKED : undefined}
                            className="h-9 px-4 border border-white/20 rounded-full text-[13px] text-white/80 hover:bg-white/5 disabled:opacity-50"
                        >
                            {dnsBusy ? 'Resolving…' : 'Resolve'}
                        </button>
                        <Badge kind="real" />
                    </div>
                    {dnsResult && (
                        <div className="mt-3 text-[12px] text-white/55">
                            {dnsResult.resolved ? (
                                <>
                                    <span className="text-white/70">{dnsResult.name}</span> →{' '}
                                    {dnsResult.addresses.join(', ')} in{' '}
                                    {dnsResult.duration_ms === null
                                        ? '—'
                                        : `${dnsResult.duration_ms.toFixed(2)} ms`}
                                </>
                            ) : (
                                <span className="text-white/50">
                                    No addresses returned for {dnsResult.name}.
                                </span>
                            )}
                        </div>
                    )}
                    {dns && (
                        <div className="mt-2 text-[11px] text-white/60">
                            Bundle lookup: {dns.name} →{' '}
                            {dns.addresses.join(', ') || 'no addresses'} (
                            {dns.duration_ms === null ? '—' : `${dns.duration_ms.toFixed(2)} ms`})
                        </div>
                    )}
                    <p className="text-white/60 text-[11px] mt-2">
                        Resolution time includes cache hits, so a repeat lookup is usually faster.
                        This uses the operating system resolver, not a remote service.
                    </p>
                </div>
            </Card>

            {/* 2 — Latency & Packet Loss */}
            <Card
                id="diag-latency"
                title="2 · Latency & Packet Loss"
                kind="real"
            >
                {ping ? (
                    <div className="space-y-4">
                        <div className="grid grid-cols-3 sm:grid-cols-6 gap-3">
                            <Metric label="Sent" value={String(ping.sent)} />
                            <Metric label="Received" value={String(ping.received)} />
                            <Metric label="Loss" value={ping.loss_pct} hint="Received replies divided by probes sent." />
                            <Metric label="Min" value={ms(ping.min_ms)} />
                            <Metric label="Avg" value={ms(ping.avg_ms)} hint="Lower bound: sub-millisecond replies are recorded as 0." />
                            <Metric label="Max" value={ms(ping.max_ms)} />
                        </div>
                        <div className="text-white/40 text-[12px]">
                            stddev {ms(ping.stddev_ms)} · target {ping.target}
                            {ping.sub_millisecond_samples > 0 &&
                                ` · ${ping.sub_millisecond_samples} sub-millisecond reply(ies) recorded as 0 ms`}
                        </div>
                        <LatencyBars samples={ping.samples_ms} />
                        {ping.notes.length > 0 && (
                            <ul className="text-white/60 text-[11px] list-disc pl-4 space-y-1">
                                {ping.notes.map((n, i) => (
                                    <li key={i}>{n}</li>
                                ))}
                            </ul>
                        )}
                    </div>
                ) : null}

                {/* Benchmark control */}
                <div className="mt-5 pt-4 border-t border-white/10">
                    <div className="flex flex-wrap items-end gap-3">
                        <label className="text-[12px] text-white/50">
                            Target
                            <input
                                value={benchTarget}
                                onChange={(e) => setBenchTarget(e.target.value)}
                                className="ml-2 bg-black/40 border border-white/15 rounded-md px-2 py-1 text-white text-[13px] w-40"
                            />
                        </label>
                        <label className="text-[12px] text-white/50">
                            Probes
                            <input
                                type="number"
                                min={1}
                                max={100}
                                value={benchCount}
                                onChange={(e) => setBenchCount(Number(e.target.value) || 1)}
                                className="ml-2 bg-black/40 border border-white/15 rounded-md px-2 py-1 text-white text-[13px] w-20"
                            />
                        </label>
                        <label className="text-[12px] text-white/50">
                            Repetitions
                            <input
                                type="number"
                                min={1}
                                max={50}
                                value={benchReps}
                                onChange={(e) => setBenchReps(Number(e.target.value) || 1)}
                                className="ml-2 bg-black/40 border border-white/15 rounded-md px-2 py-1 text-white text-[13px] w-20"
                            />
                        </label>
                        <button
                            onClick={runBenchmark}
                            disabled={benchBusy || IS_REMOTE_API}
                            title={IS_REMOTE_API ? LIVE_PROBE_BLOCKED : undefined}
                            className="h-9 px-4 border border-white/20 rounded-full text-[13px] text-white/80 hover:bg-white/5 disabled:opacity-50"
                        >
                            {benchBusy ? 'Measuring…' : 'Run benchmark'}
                        </button>
                        <Badge kind="real" />
                    </div>
                    {benchmarkRun && (
                        <div className="mt-3 text-[12px] text-white/50">
                            Stored run <span className="text-white/70">{benchmarkRun.run_id}</span> ·{' '}
                            {benchmarkRun.aggregate.received}/{benchmarkRun.aggregate.sent} replied ·{' '}
                            {pct(benchmarkRun.aggregate.packet_loss_rate)} loss
                            <button
                                onClick={() => exportRun(benchmarkRun.run_id, 'csv')}
                                className="ml-3 underline hover:text-white/80"
                            >
                                export CSV
                            </button>
                        </div>
                    )}
                </div>

                {/* Simulated fault timeline (Feature E) */}
                <div className="mt-5 pt-4 border-t border-white/10">
                    <div className="flex items-center gap-2 mb-2">
                        <span className="text-white/70 text-[13px]">Simulated fault timeline</span>
                        <Badge kind="simulated" />
                    </div>
                    <div className="flex flex-wrap items-end gap-3">
                        <label className="text-[12px] text-white/50">
                            Scenario
                            <select
                                value={faultScenario}
                                onChange={(e) => setFaultScenario(e.target.value)}
                                className="ml-2 bg-black/40 border border-white/15 rounded-md px-2 py-1 text-white text-[13px]"
                            >
                                {(scenarios.length ? scenarios : [
                                    { name: 'unreachable', description: '' },
                                    { name: 'latency_jitter', description: '' },
                                    { name: 'packet_loss', description: '' },
                                    { name: 'timeout', description: '' },
                                    { name: 'intermittent', description: '' },
                                    { name: 'healthy_to_unhealthy', description: '' },
                                ]).map((s) => (
                                    <option key={s.name} value={s.name}>{s.name}</option>
                                ))}
                            </select>
                        </label>
                        <label className="text-[12px] text-white/50">
                            Seed
                            <input
                                type="number"
                                value={faultSeed}
                                onChange={(e) => setFaultSeed(Number(e.target.value) || 0)}
                                className="ml-2 bg-black/40 border border-white/15 rounded-md px-2 py-1 text-white text-[13px] w-28"
                            />
                        </label>
                        <button
                            onClick={runFault}
                            disabled={faultBusy}
                            className="h-9 px-4 border border-white/20 rounded-full text-[13px] text-white/80 hover:bg-white/5 disabled:opacity-50"
                        >
                            {faultBusy ? 'Simulating…' : 'Run scenario'}
                        </button>
                    </div>
                    {fault && <FaultTimeline result={fault} />}
                </div>
            </Card>

            {/* 3 — Route Trace */}
            <Card
                id="diag-route"
                title="3 · Route Trace"
                kind="real"
            >
                {trace ? (
                    trace.hops.length ? (
                        <div className="border border-white/10 rounded-lg overflow-hidden">
                            <table className="w-full text-[13px]">
                                <thead>
                                    <tr className="bg-white/[0.03] text-white/60">
                                        <th className="text-left px-3 py-2 font-normal">Hop</th>
                                        <th className="text-left px-3 py-2 font-normal">Node</th>
                                        <th className="text-right px-3 py-2 font-normal">RTT</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {trace.hops.map((h) => (
                                        <tr key={h.hop} className="border-t border-white/10">
                                            <td className="px-3 py-2 text-white/50">{h.hop}</td>
                                            <td className="px-3 py-2 text-white/70">
                                                {h.timed_out ? <span className="text-white/60">* * * request timed out</span> : h.node}
                                            </td>
                                            <td className="px-3 py-2 text-right text-white/60 tabular-nums">
                                                {h.timed_out ? '—' : h.sub_millisecond && h.rtt_ms === 0 ? '<1 ms' : ms(h.rtt_ms)}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    ) : (
                        <Empty>The trace returned no hops.</Empty>
                    )
                ) : run?.sections.traceroute?.status === 'error' ? (
                    <ErrorNote message={run.sections.traceroute.error || 'Route trace failed.'} />
                ) : null}
            </Card>

            {/* 4 — Network Adapters */}
            <Card
                id="diag-adapters"
                title="4 · Network Adapters"
                kind="real"
            >
                {network?.adapters && network.adapters.available ? (
                    network.adapters.adapters.length ? (
                        <div className="border border-white/10 rounded-lg overflow-hidden">
                            <table className="w-full text-[13px]">
                                <thead>
                                    <tr className="bg-white/[0.03] text-white/60">
                                        <th className="text-left px-3 py-2 font-normal">Name</th>
                                        <th className="text-left px-3 py-2 font-normal">Status</th>
                                        <th className="text-left px-3 py-2 font-normal">Link</th>
                                        <th className="text-left px-3 py-2 font-normal">MAC</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {network.adapters.adapters.map((a, i) => (
                                        <tr key={`${a.name}-${i}`} className="border-t border-white/10">
                                            <td className="px-3 py-2 text-white/70">{a.name}</td>
                                            <td className="px-3 py-2">
                                                <span className={a.status?.toLowerCase() === 'up' ? 'text-emerald-300' : 'text-white/40'}>
                                                    {a.status}
                                                </span>
                                            </td>
                                            <td className="px-3 py-2 text-white/50">{a.link_speed}</td>
                                            <td className="px-3 py-2 text-white/60 font-mono text-[11px]">{a.mac_address}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    ) : (
                        <Empty>No adapters were reported.</Empty>
                    )
                ) : network?.adapters?.reason ? (
                    <Empty>Adapter enumeration unavailable: {network.adapters.reason}</Empty>
                ) : null}

                {network?.ip_configuration?.available && network.ip_configuration.interfaces.length > 0 && (
                    <div className="mt-4">
                        <div className="text-white/60 text-[12px] mb-2">IPv4 configuration</div>
                        <div className="border border-white/10 rounded-lg overflow-hidden">
                            <table className="w-full text-[13px]">
                                <thead>
                                    <tr className="bg-white/[0.03] text-white/60">
                                        <th className="text-left px-3 py-2 font-normal">Interface</th>
                                        <th className="text-left px-3 py-2 font-normal">IPv4</th>
                                        <th className="text-left px-3 py-2 font-normal">Gateway</th>
                                        <th className="text-left px-3 py-2 font-normal">DNS</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {network.ip_configuration.interfaces.map((f, i) => (
                                        <tr key={`${f.alias}-${i}`} className="border-t border-white/10">
                                            <td className="px-3 py-2 text-white/70">{f.alias}</td>
                                            <td className="px-3 py-2 text-white/50 font-mono text-[11px]">{f.ipv4_addresses.join(', ')}</td>
                                            <td className="px-3 py-2 text-white/50">{f.default_gateway ?? '—'}</td>
                                            <td className="px-3 py-2 text-white/40 font-mono text-[11px]">{f.dns_servers.join(', ') || '—'}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}
            </Card>

            {/* 5 — Benchmark History */}
            <Card
                id="diag-history"
                title="5 · Benchmark History"
                kind="controlled"
                right={
                    <div className="flex gap-2">
                        <button onClick={refreshHistory} className="h-8 px-3 border border-white/20 rounded-full text-[12px] text-white/70 hover:bg-white/5">
                            Refresh
                        </button>
                        <button onClick={clearHistory} className="h-8 px-3 border border-white/20 rounded-full text-[12px] text-white/70 hover:bg-white/5">
                            Clear
                        </button>
                    </div>
                }
            >
                {historyEmpty ? null : (
                    <>
                        <div className="border border-white/10 rounded-lg overflow-x-auto">
                            <table className="w-full text-[12px] whitespace-nowrap">
                                <thead>
                                    <tr className="bg-white/[0.03] text-white/60">
                                        <th className="px-2 py-2" aria-label="Select" />
                                        <th className="text-left px-2 py-2 font-normal">Run</th>
                                        <th className="text-left px-2 py-2 font-normal">Kind</th>
                                        <th className="text-left px-2 py-2 font-normal">Provenance</th>
                                        <th className="text-left px-2 py-2 font-normal">Target / scenario</th>
                                        <th className="text-right px-2 py-2 font-normal">Loss</th>
                                        <th className="text-right px-2 py-2 font-normal">Avg</th>
                                        <th className="text-right px-2 py-2 font-normal">Export</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {history.map((r) => (
                                        <tr key={r.run_id} className="border-t border-white/10">
                                            <td className="px-2 py-2 text-center">
                                                <input
                                                    type="checkbox"
                                                    aria-label={`Select run ${r.run_id}`}
                                                    checked={selected.includes(r.run_id)}
                                                    onChange={() => toggleSelected(r.run_id)}
                                                />
                                            </td>
                                            <td className="px-2 py-2 text-white/60 font-mono">{r.run_id.slice(0, 18)}</td>
                                            <td className="px-2 py-2 text-white/45">{r.kind}</td>
                                            <td className="px-2 py-2"><Badge kind={r.simulated ? 'simulated' : 'real'} /></td>
                                            <td className="px-2 py-2 text-white/45">{r.target ?? r.scenario ?? '—'}</td>
                                            <td className="px-2 py-2 text-right text-white/55 tabular-nums">{r.packet_loss_pct ? `${r.packet_loss_pct}%` : '—'}</td>
                                            <td className="px-2 py-2 text-right text-white/55 tabular-nums">{r.avg_ms === null ? '—' : r.avg_ms.toFixed(2)}</td>
                                            <td className="px-2 py-2 text-right">
                                                <button onClick={() => exportRun(r.run_id, 'csv')} className="underline text-white/45 hover:text-white/80">CSV</button>
                                                <span className="text-white/60 mx-1">·</span>
                                                <button onClick={() => exportRun(r.run_id, 'json')} className="underline text-white/45 hover:text-white/80">JSON</button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                        <div className="flex items-center gap-3 mt-3">
                            <button
                                onClick={compareSelected}
                                disabled={selected.length < 2}
                                className="h-9 px-4 bg-white text-black rounded-full text-[13px] font-medium disabled:opacity-40"
                            >
                                Compare {selected.length} selected
                            </button>
                            <span className="text-white/60 text-[11px]">{historyNote}</span>
                        </div>
                    </>
                )}

                {comparison && (
                    <div className="mt-4 border border-white/10 rounded-lg overflow-x-auto">
                        <table className="w-full text-[12px] whitespace-nowrap">
                            <thead>
                                <tr className="bg-white/[0.03] text-white/60">
                                    <th className="text-left px-3 py-2 font-normal">Run</th>
                                    <th className="text-left px-3 py-2 font-normal">Provenance</th>
                                    <th className="text-right px-3 py-2 font-normal">Loss %</th>
                                    <th className="text-right px-3 py-2 font-normal">Min</th>
                                    <th className="text-right px-3 py-2 font-normal">Avg</th>
                                    <th className="text-right px-3 py-2 font-normal">Max</th>
                                </tr>
                            </thead>
                            <tbody>
                                {comparison.runs.map((row) => (
                                    <tr key={row.run_id} className="border-t border-white/10">
                                        <td className="px-3 py-2 text-white/60 font-mono">{row.run_id.slice(0, 18)}</td>
                                        <td className="px-3 py-2"><Badge kind={row.data_kind} /></td>
                                        <td className="px-3 py-2 text-right text-white/55 tabular-nums">{row.packet_loss_pct ?? '—'}</td>
                                        <td className="px-3 py-2 text-right text-white/55 tabular-nums">{row.min_ms ?? '—'}</td>
                                        <td className="px-3 py-2 text-right text-white/55 tabular-nums">{row.avg_ms ?? '—'}</td>
                                        <td className="px-3 py-2 text-right text-white/55 tabular-nums">{row.max_ms ?? '—'}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        <ul className="list-disc pl-5 py-3 text-white/40 text-[11px] space-y-1">
                            {comparison.notes.map((n, i) => (
                                <li key={i}>{n}</li>
                            ))}
                        </ul>
                    </div>
                )}
            </Card>

            {/* 6 — Hysteresis Sweep */}
            <Card
                id="diag-sweep"
                title="6 · Hysteresis Sweep"
                kind="controlled"
                right={
                    <button
                        onClick={runSweep}
                        disabled={sweepBusy}
                        className="h-9 px-4 bg-white text-black rounded-full text-[13px] font-medium disabled:opacity-50"
                    >
                        {sweepBusy ? 'Sweeping…' : 'Run sweep'}
                    </button>
                }
            >
                {sweep ? (
                    <div className="space-y-4">
                        <div className="border border-white/10 rounded-lg overflow-x-auto">
                            <table className="w-full text-[12px] whitespace-nowrap">
                                <thead>
                                    <tr className="bg-white/[0.03] text-white/60">
                                        <th className="text-right px-3 py-2 font-normal">Ratio</th>
                                        <th className="text-right px-3 py-2 font-normal">R / F</th>
                                        <th className="text-right px-3 py-2 font-normal">Flaps</th>
                                        <th className="text-right px-3 py-2 font-normal">Recovery (s)</th>
                                        <th className="text-right px-3 py-2 font-normal">Detection (s)</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {sweep.points.map((p) => (
                                        <tr key={p.ratio} className="border-t border-white/10">
                                            <td className="px-3 py-2 text-right text-white/70 tabular-nums">{p.ratio}</td>
                                            <td className="px-3 py-2 text-right text-white/45 tabular-nums">{p.r_thresh} / {p.f_thresh}</td>
                                            <td className="px-3 py-2 text-right text-white/55 tabular-nums">{p.flap_count}</td>
                                            <td className="px-3 py-2 text-right text-white/55 tabular-nums">{p.recovery_s === null ? '—' : p.recovery_s.toFixed(2)}</td>
                                            <td className="px-3 py-2 text-right text-white/55 tabular-nums">{p.detection_s === null ? '—' : p.detection_s.toFixed(2)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                        <SweepChart points={sweep.points} frontier={sweep.pareto_frontier} />
                        <div className="text-white/40 text-[12px]">
                            Naive single-threshold baseline: {sweep.naive.flap_count} flaps
                            {sweep.naive.recovery_s !== null && `, ${sweep.naive.recovery_s.toFixed(2)} s recovery`}.
                        </div>
                        <ul className="list-disc pl-5 text-white/40 text-[11px] space-y-1">
                            {sweep.findings.map((f, i) => <li key={i}>{f}</li>)}
                        </ul>
                        <p className="text-white/60 text-[11px]">{sweep.provenance}</p>
                    </div>
                ) : (
                    <Empty>Run the sweep to see the stability-versus-recovery trade-off for ratios 1.0 to 3.0.</Empty>
                )}
            </Card>

            {/* 7 — Scheduler Comparison */}
            <Card
                id="diag-scheduler"
                title="7 · Scheduler Comparison"
                kind="controlled"
                right={
                    <button
                        onClick={runScheduler}
                        disabled={schedulerBusy}
                        className="h-9 px-4 bg-white text-black rounded-full text-[13px] font-medium disabled:opacity-50"
                    >
                        {schedulerBusy ? 'Comparing…' : 'Run comparison'}
                    </button>
                }
            >
                {scheduler ? (
                    <div className="space-y-4">
                        <div className="border border-white/10 rounded-lg overflow-x-auto">
                            <table className="w-full text-[12px] whitespace-nowrap">
                                <thead>
                                    <tr className="bg-white/[0.03] text-white/60">
                                        <th className="text-left px-3 py-2 font-normal">Variant</th>
                                        <th className="text-left px-3 py-2 font-normal">RTX</th>
                                        <th className="text-right px-3 py-2 font-normal">Reps</th>
                                        <th className="text-right px-3 py-2 font-normal">Duration p50 (s)</th>
                                        <th className="text-right px-3 py-2 font-normal">Goodput p50 (bps)</th>
                                        <th className="text-right px-3 py-2 font-normal">Out-of-order p50</th>
                                        <th className="text-left px-3 py-2 font-normal">Integrity</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {scheduler.rows.map((row) => (
                                        <tr key={`${row.variant}-${row.retransmit_policy}`} className="border-t border-white/10">
                                            <td className="px-3 py-2 text-white/70">{row.variant}</td>
                                            <td className="px-3 py-2 text-white/45">{row.retransmit_policy}</td>
                                            <td className="px-3 py-2 text-right text-white/50 tabular-nums">{row.repetitions}</td>
                                            <td className="px-3 py-2 text-right text-white/55 tabular-nums">{row.duration_s_median?.toFixed(4) ?? '—'}</td>
                                            <td className="px-3 py-2 text-right text-white/55 tabular-nums">{row.goodput_bps_median ? Math.round(row.goodput_bps_median).toLocaleString() : '—'}</td>
                                            <td className="px-3 py-2 text-right text-white/55 tabular-nums">{row.out_of_order_median ?? '—'}</td>
                                            <td className="px-3 py-2 text-white/50">{row.integrity_ok}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                        <ul className="list-disc pl-5 text-white/40 text-[11px] space-y-1">
                            {scheduler.findings.map((f, i) => <li key={i}>{f}</li>)}
                        </ul>
                        <p className="text-white/60 text-[11px]">{scheduler.provenance}</p>
                    </div>
                ) : (
                    <Empty>Run the comparison to rank scheduler variants under identical, declared conditions.</Empty>
                )}
            </Card>

            {/* 8 — Export Reports */}
            <Card
                id="diag-export"
                title="8 · Export Reports"
                kind="real"
            >
                {latestRunId ? (
                    <div className="flex flex-wrap items-center gap-3">
                        <span className="text-white/50 text-[13px]">Latest run {latestRunId}</span>
                        <button
                            onClick={() => exportRun(latestRunId, 'json')}
                            className="h-9 px-4 border border-white/20 rounded-full text-[13px] text-white/80 hover:bg-white/5"
                        >
                            Download JSON
                        </button>
                        <button
                            onClick={() => exportRun(latestRunId, 'csv')}
                            className="h-9 px-4 border border-white/20 rounded-full text-[13px] text-white/80 hover:bg-white/5"
                        >
                            Download CSV
                        </button>
                    </div>
                ) : (
                    <Empty>Run diagnostics first; the export then names the run and its configuration hash.</Empty>
                )}

                {status && (
                    <div className="mt-5 pt-4 border-t border-white/10 grid sm:grid-cols-2 gap-4">
                        <div>
                            <div className="text-white/60 text-[13px] mb-2">Real measurements available here</div>
                            <ul className="text-white/45 text-[12px] space-y-1">
                                {realCaps.map(([name, cap]) => (
                                    <li key={name} className="flex items-center gap-2">
                                        <span className={cap.supported ? 'text-emerald-300' : 'text-white/60'}>
                                            {cap.supported ? '✓' : '✕'}
                                        </span>
                                        <span>{name}</span>
                                        <span className="text-white/60">{cap.command}</span>
                                    </li>
                                ))}
                            </ul>
                        </div>
                        <div>
                            <div className="text-white/60 text-[13px] mb-2">Not available here</div>
                            <ul className="text-white/45 text-[12px] space-y-1">
                                {Object.entries(unsupported).map(([name, cap]) => (
                                    <li key={name}>
                                        <span className="text-white/70">{name}</span>: {cap.reason}
                                    </li>
                                ))}
                                {controlled && <li className="text-white/60">{controlled.note}</li>}
                            </ul>
                        </div>
                    </div>
                )}
            </Card>
        </div>
    );
}

/** Coloured probe timeline plus the healthy/unhealthy transitions between them. */
function FaultTimeline({ result }: { result: FaultScenarioResult }) {
    const label =
        `Simulated timeline over ${result.total_probes} probes for scenario ` +
        `${result.scenario}: ${result.healthy_count} healthy, ${result.unhealthy_count} ` +
        `unhealthy, ${result.transition_count} transitions.`;
    return (
        <div className="mt-3">
            <div className="flex flex-wrap items-center gap-3 text-white/50 text-[12px]">
                <span>{result.healthy_count} healthy</span>
                <span>{result.unhealthy_count} unhealthy</span>
                <span>{result.transition_count} transitions</span>
                <span className="text-white/60">seed {result.seed}</span>
                <span className="font-mono text-white/60">config {result.config_hash.slice(0, 12)}…</span>
            </div>
            <div className="flex items-end gap-[2px] h-16 mt-2" role="img" aria-label={label}>
                {result.probes.map((p) => (
                    <div
                        key={p.index}
                        className={`flex-1 rounded-sm ${p.ok ? 'bg-emerald-400/60' : 'bg-red-400/60'}`}
                        style={{ height: p.ok && p.latency_ms ? `${Math.min(100, 20 + p.latency_ms)}%` : '30%' }}
                        title={`Probe ${p.index + 1}: ${p.state}${p.latency_ms !== null ? ` ${p.latency_ms.toFixed(2)} ms` : ''}`}
                    />
                ))}
            </div>
            {result.transitions.length > 0 && (
                <div className="text-white/60 text-[11px] mt-1">
                    Transitions at probes:{' '}
                    {result.transitions.map((t) => `${t.index} (${t.from}→${t.to})`).join(', ')}
                </div>
            )}
            <ul className="list-disc pl-5 text-white/60 text-[11px] mt-2 space-y-1">
                {result.limitations.map((l, i) => <li key={i}>{l}</li>)}
            </ul>
        </div>
    );
}
