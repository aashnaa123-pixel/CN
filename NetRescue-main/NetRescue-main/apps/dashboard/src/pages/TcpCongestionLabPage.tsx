/**
 * TcpCongestionLabPage — Feature F: TCP congestion-control simulator.
 *
 * Runs the backend's discrete-event TCP Reno model and plots its output:
 * congestion window and ssthresh over time, goodput per RTT-sized bin, and the
 * loss-response events (fast retransmit, timeout, drops). Scenarios can be
 * collected and compared. Everything plotted is the API response.
 */
import React, { useState } from 'react';
import { Play, Plus } from 'lucide-react';
import { labsApi, type TcpCompare, type TcpResult } from '../labs/api';
import {
    Badge, Bars, Button, Card, COLOURS, ErrorBanner, ExecutionBadge, Field, fmt, LabPage, LineChart, Metric, Notes,
    StaleBanner, useAction, useLabResult,
} from '../labs/ui';

interface Form {
    rtt_ms: number; bandwidth_mbps: number; loss_rate: number; buffer_packets: number;
    duration_s: number; initial_cwnd: number; initial_ssthresh: number; seed: number;
}

const DEFAULT: Form = {
    rtt_ms: 80, bandwidth_mbps: 10, loss_rate: 0, buffer_packets: 40,
    duration_s: 20, initial_cwnd: 1, initial_ssthresh: 64, seed: 1,
};

const PRESETS: { label: string; patch: Partial<Form> }[] = [
    { label: 'Clean path', patch: { loss_rate: 0, rtt_ms: 40 } },
    { label: 'Long RTT', patch: { loss_rate: 0, rtt_ms: 250 } },
    { label: '1% random loss', patch: { loss_rate: 0.01, rtt_ms: 80 } },
    { label: '5% random loss', patch: { loss_rate: 0.05, rtt_ms: 80 } },
    { label: 'Small buffer', patch: { loss_rate: 0, buffer_packets: 8 } },
    { label: 'Narrow link', patch: { loss_rate: 0, bandwidth_mbps: 2 } },
];

export default function TcpCongestionLabPage() {
    const [form, setForm] = useState<Form>(DEFAULT);
    const [shelf, setShelf] = useState<{ label: string; form: Form }[]>([]);
    const act = useAction();
    const set = (k: keyof Form) => (v: number) => setForm({ ...form, [k]: v });
    const formKey = JSON.stringify(form);
    const run1 = useLabResult<TcpResult>(formKey);
    const shelfKey = JSON.stringify(shelf);
    const cmp = useLabResult<TcpCompare>(shelfKey);
    const result = run1.result;
    const compare = cmp.result;
    const invalid = Object.values(form).some(v => !Number.isFinite(v));

    const run = () => act.run(async () => run1.set(await labsApi.tcp({ ...form }), formKey));
    const addToShelf = (label?: string) => {
        if (shelf.length >= 6) return act.setError('Compare at most 6 scenarios.');
        setShelf([...shelf, { label: label ?? `S${shelf.length + 1}: ${form.rtt_ms}ms ${(form.loss_rate * 100).toFixed(1)}% ${form.bandwidth_mbps}Mb`, form: { ...form } }]);
    };
    const runCompare = () => act.run(async () =>
        cmp.set(await labsApi.tcpCompare(shelf.map(s => ({ ...s.form, label: s.label }))), shelfKey));

    const s = result?.summary;
    const markers = (result?.events ?? [])
        .filter(e => e.type === 'timeout' || e.type === 'fast_retransmit')
        .map(e => ({ x: e.t, label: e.type === 'timeout' ? 'timeout' : 'fast retransmit',
                     colour: e.type === 'timeout' ? COLOURS.bad : COLOURS.b }));
    const uniqueMarkers = markers.filter((m, i) => markers.findIndex(o => o.label === m.label) === i);

    return (
        <LabPage title="TCP Congestion-Control Lab" experiment="tcp"
            subtitle="A packet-level discrete-event model of one TCP Reno flow over one bottleneck. It shows how the congestion window reacts to slow start, congestion avoidance and loss."
            back={{ to: '/features', label: 'Back to features' }}>
            <ErrorBanner error={act.error} onClose={() => act.setError(null)} />

            <Card title="Scenario" badge={<Badge kind="simulation" />}>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <Field label="RTT (ms)" value={form.rtt_ms} min={0.1} max={2000} onChange={set('rtt_ms')} />
                    <Field label="Bandwidth (Mbit/s)" value={form.bandwidth_mbps} min={0.1} max={100} onChange={set('bandwidth_mbps')} />
                    <Field label="Random loss (0–0.49)" value={form.loss_rate} min={0} max={0.49} step={0.005} onChange={set('loss_rate')} />
                    <Field label="Router buffer (packets)" value={form.buffer_packets} min={1} max={5000} onChange={set('buffer_packets')} />
                    <Field label="Duration (s)" value={form.duration_s} min={0.5} max={120} onChange={set('duration_s')} />
                    <Field label="Initial cwnd (segments)" value={form.initial_cwnd} min={1} max={100} onChange={set('initial_cwnd')} />
                    <Field label="Initial ssthresh" value={form.initial_ssthresh} min={2} max={10000} onChange={set('initial_ssthresh')} />
                    <Field label="Seed" value={form.seed} min={0} onChange={set('seed')} hint="Same seed + parameters = identical run" />
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">
                    {PRESETS.map(p => (
                        <button key={p.label} onClick={() => setForm({ ...DEFAULT, ...p.patch })}
                            className="h-7 px-3 rounded-full border border-white/10 text-[11px] text-white/55 hover:text-white hover:border-white/30">
                            {p.label}
                        </button>
                    ))}
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                    <Button onClick={run} disabled={act.loading || invalid}><Play size={12} /> {act.loading ? 'Simulating…' : 'Run simulation'}</Button>
                    <Button variant="ghost" onClick={() => addToShelf()}><Plus size={12} /> Add to comparison</Button>
                </div>
                {invalid && <p role="alert" className="mt-2 text-[12px] text-amber-300">Every field needs a number.</p>}
            </Card>

            {!result && !act.loading && <p className="text-[12px] text-white/45">No result yet — set a scenario and press “Run simulation”.</p>}
            {result && s && (
                <>
                    <StaleBanner stale={run1.stale} onRerun={invalid ? undefined : run} />
                    <ExecutionBadge execution={result.execution} />
                    <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2">
                        <Metric label="Goodput" value={fmt(s.goodput_mbps)} unit="Mbit/s" />
                        <Metric label="Link utilisation" value={fmt(Number(s.link_utilisation) * 100, 1)} unit="%" />
                        <Metric label="Max cwnd" value={fmt(s.max_cwnd_segments, 1)} unit="seg" />
                        <Metric label="Retransmissions" value={String(s.retransmissions)} />
                        <Metric label="Fast retransmits" value={String(s.fast_retransmits)} />
                        <Metric label="Timeouts" value={String(s.timeouts)} />
                        <Metric label="Queue drops" value={String(s.queue_drops)} hint="Dropped because the router buffer was full" />
                        <Metric label="Random drops" value={String(s.random_drops)} hint="Dropped by the random-loss model" />
                        <Metric label="Mean RTT" value={fmt(s.rtt_mean_ms, 1)} unit="ms" hint="Includes queueing delay" />
                        <Metric label="Final ssthresh" value={fmt(s.final_ssthresh_segments, 1)} unit="seg" />
                    </div>
                    <div className="grid gap-5 lg:grid-cols-2">
                        <Card title="Congestion window" badge={<Badge kind="simulation" />}>
                            <LineChart xLabel="time (s)" yLabel="segments" markers={uniqueMarkers}
                                series={[
                                    { name: 'cwnd', colour: COLOURS.a, points: result.cwnd_series.map(r => [r[0], r[1]]), step: true },
                                    { name: 'ssthresh', colour: COLOURS.e, dashed: true, step: true,
                                      points: result.cwnd_series.map(r => [r[0], Math.min(r[2], 400)]) },
                                ]} />
                            <PhaseStrip rows={result.cwnd_series} />
                        </Card>
                        <Card title="Goodput" badge={<Badge kind="simulation" />}>
                            <LineChart xLabel="time (s)" yLabel="Mbit/s (per RTT bin)"
                                series={[{ name: 'acknowledged goodput', colour: COLOURS.c, points: result.throughput_series, step: true },
                                         { name: 'link rate', colour: 'rgba(255,255,255,0.35)', dashed: true,
                                           points: [[0, Number(result.params.bandwidth_mbps)], [result.throughput_series[result.throughput_series.length - 1]?.[0] ?? 1, Number(result.params.bandwidth_mbps)]] }]} />
                        </Card>
                    </div>
                    <Card title={`Loss events (${result.events.length}${result.events.length >= 800 ? '+, truncated' : ''})`}>
                        <div className="max-h-56 overflow-auto">
                            <table className="w-full text-[12px]">
                                <thead><tr className="text-left text-white/40"><th className="font-normal py-1 pr-3">t (s)</th><th className="font-normal pr-3">Event</th><th className="font-normal">Detail</th></tr></thead>
                                <tbody>
                                    {result.events.slice(0, 60).map((e, i) => (
                                        <tr key={i} className="border-t border-white/5">
                                            <td className="py-1 pr-3 text-white/50">{e.t}</td>
                                            <td className="pr-3 text-white/80">{e.type.replace(/_/g, ' ')}</td>
                                            <td className="text-white/45">{Object.entries(e).filter(([k]) => k !== 't' && k !== 'type').map(([k, v]) => `${k}=${String(v)}`).join(' ')}</td>
                                        </tr>
                                    ))}
                                    {result.events.length === 0 && <tr><td colSpan={3} className="py-2 text-white/40">No loss events in this run.</td></tr>}
                                </tbody>
                            </table>
                        </div>
                    </Card>
                </>
            )}

            <Card title="Scenario comparison" badge={<Badge kind="simulation" />}>
                {shelf.length === 0 ? (
                    <p className="text-[12px] text-white/45">Set the parameters above and use “Add to comparison” (up to 6) to compare RTT, loss and bandwidth cases under the same model.</p>
                ) : (
                    <div className="space-y-3">
                        <div className="flex flex-wrap gap-1.5">
                            {shelf.map((sc, i) => (
                                <span key={i} className="inline-flex items-center gap-1 h-7 pl-2.5 pr-1 rounded-full border border-white/15 text-[11px] text-white/70">
                                    {sc.label}
                                    <button aria-label={`Remove ${sc.label}`} onClick={() => setShelf(shelf.filter((_, j) => j !== i))} className="w-5 h-5 text-white/40 hover:text-red-300">✕</button>
                                </span>
                            ))}
                        </div>
                        <Button onClick={runCompare} disabled={act.loading}>Run comparison</Button>
                    </div>
                )}
                {compare && (
                    <div className="mt-4 space-y-4">
                        <StaleBanner stale={cmp.stale} onRerun={runCompare} />
                        <ExecutionBadge execution={compare.execution} />
                        <Bars unit="Mbit/s" items={compare.table.map((r, i) => ({ label: String(r.label), value: Number(r.goodput_mbps), colour: Object.values(COLOURS)[i % 5] }))} />
                        <div className="overflow-x-auto">
                            <table className="w-full text-[12px]">
                                <thead><tr className="text-left text-white/40">{['Scenario', 'RTT', 'Loss', 'Link', 'Goodput', 'Util.', 'Retx', 'RTOs', 'Max cwnd'].map(h => <th key={h} className="font-normal py-1 pr-3">{h}</th>)}</tr></thead>
                                <tbody>{compare.table.map((r, i) => (
                                    <tr key={i} className="border-t border-white/5 text-white/70">
                                        <td className="py-1 pr-3">{String(r.label)}</td><td className="pr-3">{r.rtt_ms}</td><td className="pr-3">{r.loss_rate}</td>
                                        <td className="pr-3">{r.bandwidth_mbps}</td><td className="pr-3">{fmt(r.goodput_mbps)}</td><td className="pr-3">{fmt(Number(r.link_utilisation) * 100, 1)}%</td>
                                        <td className="pr-3">{r.retransmissions}</td><td className="pr-3">{r.timeouts}</td><td>{fmt(r.max_cwnd_segments, 1)}</td>
                                    </tr>))}</tbody>
                            </table>
                        </div>
                        <p className="text-[12px] text-white/55">{compare.finding}</p>
                    </div>
                )}
            </Card>

            <Notes title="Model and assumptions" items={result?.assumptions ?? [
                'Variant: TCP Reno (RFC 5681) with the RFC 6298 retransmission timer — no behaviour of other variants is mixed in.',
                'One bulk flow over one FIFO drop-tail bottleneck; ACKs are immediate, cumulative and never lost.',
                'A simulation, not a kernel experiment. Run a simulation to see the full list returned by the API.',
            ]} />
        </LabPage>
    );
}

/** A coloured strip showing which congestion-control phase the sender was in. */
function PhaseStrip({ rows }: { rows: TcpResult['cwnd_series'] }) {
    if (rows.length < 2) return null;
    const total = rows[rows.length - 1][0] || 1;
    const colour: Record<string, string> = {
        slow_start: COLOURS.c, congestion_avoidance: COLOURS.a, fast_recovery: COLOURS.b,
    };
    return (
        <div className="mt-2">
            <div className="flex h-2 rounded-full overflow-hidden bg-white/5" aria-label="Congestion-control phase over time">
                {rows.slice(0, -1).map((r, i) => (
                    <div key={i} style={{ width: `${((rows[i + 1][0] - r[0]) / total) * 100}%`, background: colour[r[3]] ?? '#fff' }} />
                ))}
            </div>
            <div className="flex gap-4 mt-1 text-[11px] text-white/50">
                <span><i className="inline-block w-2 h-2 rounded-full mr-1" style={{ background: COLOURS.c }} />slow start</span>
                <span><i className="inline-block w-2 h-2 rounded-full mr-1" style={{ background: COLOURS.a }} />congestion avoidance</span>
                <span><i className="inline-block w-2 h-2 rounded-full mr-1" style={{ background: COLOURS.b }} />fast recovery</span>
            </div>
        </div>
    );
}
