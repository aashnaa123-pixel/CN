/**
 * IntegrationLabPage — hysteresis detection → Dijkstra rerouting → TCP transfer.
 *
 * The backend feeds a heartbeat sequence to the same HysteresisStateMachine the
 * recovery lab uses, converts each state change into a routing-graph update
 * (SUSPECT raises the link cost, FAILED takes it down), re-runs Dijkstra, and
 * simulates the same TCP transfer over the baseline, degraded-not-rerouted and
 * rerouted paths. Results can be exported as JSON or CSV.
 */
import React, { useState } from 'react';
import { Play } from 'lucide-react';
import { labsApi, type IntegrationResult } from '../labs/api';
import {
    Badge, Bars, Button, Card, COLOURS, download, ErrorBanner, ExecutionBadge, Field, fmt, LabPage, LineChart, Metric, Notes,
    StaleBanner, useAction, useLabResult,
} from '../labs/ui';

const STATE_COLOUR: Record<string, string> = { HEALTHY: COLOURS.c, SUSPECT: COLOURS.b, FAILED: COLOURS.bad };

interface Form {
    outage_start: number; outage_len: number; total_ticks: number;
    f_thresh: number; n: number; r_thresh: number; use_ewma: boolean; ewma_alpha: number;
    suspect_cost_multiplier: number; degraded_loss: number; transfer_bytes: number;
}
const DEFAULT: Form = {
    outage_start: 8, outage_len: 14, total_ticks: 40, f_thresh: 3, n: 5, r_thresh: 5,
    use_ewma: false, ewma_alpha: 0.3, suspect_cost_multiplier: 3, degraded_loss: 0.03, transfer_bytes: 3_000_000,
};

export default function IntegrationLabPage() {
    const [form, setForm] = useState<Form>(DEFAULT);
    const act = useAction();
    const set = (k: keyof Form) => (v: number) => setForm({ ...form, [k]: v });
    const formKey = JSON.stringify(form);
    const ran = useLabResult<IntegrationResult>(formKey);
    const res = ran.result;
    const invalid = Object.entries(form).some(([, v]) => typeof v === 'number' && !Number.isFinite(v));
    const run = () => act.run(async () => ran.set(await labsApi.integration({ ...form }), formKey));

    const names: Record<string, string> = {
        baseline: 'Baseline (healthy)', degraded_not_rerouted: 'Degraded, not rerouted', after_reroute: 'After reroute',
    };

    return (
        <LabPage title="Detect → Reroute → Transfer" experiment="integration"
            subtitle="One experiment across the labs: the hysteresis detector decides a link is degraded, Dijkstra recalculates the route, and the TCP model shows the effect on a transfer."
            back={{ to: '/features', label: 'Back to features' }}>
            <ErrorBanner error={act.error} onClose={() => act.setError(null)} />

            <Card title="Experiment" badge={<Badge kind="simulation" />}>
                <p className="text-[12px] text-white/50 mb-3">
                    Topology: the routing lab's example graph, A → F, monitoring link B-D. Heartbeats B-D are generated: healthy,
                    then an outage, then healthy again.
                </p>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <Field label="Outage starts at tick" value={form.outage_start} min={0} onChange={set('outage_start')} />
                    <Field label="Outage length (ticks)" value={form.outage_len} min={0} onChange={set('outage_len')} />
                    <Field label="Total ticks" value={form.total_ticks} min={5} max={400} onChange={set('total_ticks')} />
                    <Field label="f_thresh (→ SUSPECT)" value={form.f_thresh} min={1} onChange={set('f_thresh')} />
                    <Field label="n (→ FAILED)" value={form.n} min={2} onChange={set('n')} />
                    <Field label="r_thresh (→ HEALTHY)" value={form.r_thresh} min={1} onChange={set('r_thresh')} />
                    <Field label="SUSPECT cost multiplier" value={form.suspect_cost_multiplier} min={1} onChange={set('suspect_cost_multiplier')} hint="Link cost is multiplied by this while SUSPECT" />
                    <Field label="Degraded link loss" value={form.degraded_loss} min={0} max={0.49} step={0.01} onChange={set('degraded_loss')} hint="Loss on B-D while traffic is NOT moved" />
                    <Field label="Transfer size (bytes)" value={form.transfer_bytes} min={10000} onChange={set('transfer_bytes')} />
                    <label className="flex items-center gap-2 text-[12px] text-white/60 pt-5">
                        <input type="checkbox" checked={form.use_ewma} onChange={e => setForm({ ...form, use_ewma: e.target.checked })} /> EWMA filter
                    </label>
                    {form.use_ewma && <Field label="EWMA alpha" value={form.ewma_alpha} min={0.01} max={1} step={0.05} onChange={set('ewma_alpha')} />}
                </div>
                <div className="mt-4"><Button onClick={run} disabled={act.loading || invalid}><Play size={12} /> {act.loading ? 'Running…' : 'Run experiment'}</Button></div>
                {invalid && <p role="alert" className="mt-2 text-[12px] text-amber-300">Every field needs a number.</p>}
            </Card>

            {!res && !act.loading && <p className="text-[12px] text-white/45">No result yet — define the outage and press “Run experiment”.</p>}
            {res && (
                <>
                    <StaleBanner stale={ran.stale} onRerun={invalid ? undefined : run} />
                    <ExecutionBadge execution={res.execution} />
                    <p className="text-[12px] text-white/50">{res.label}</p>

                    <Card title="1 · Hysteresis detector on link B-D">
                        <div className="flex gap-px h-8" role="img" aria-label="Heartbeat result and detector state per tick">
                            {res.states.map((s, i) => (
                                <div key={i} className="flex-1 min-w-[3px] relative" title={`tick ${i + 1}: ${res.heartbeats[i] ? 'missed' : 'received'} → ${s}`}
                                    style={{ background: STATE_COLOUR[s] }}>
                                    {res.heartbeats[i] && <span className="absolute inset-x-0 top-0 h-1.5 bg-black/70" />}
                                </div>
                            ))}
                        </div>
                        <div className="flex flex-wrap gap-4 mt-2 text-[11px] text-white/50">
                            {Object.entries(STATE_COLOUR).map(([k, c]) => <span key={k}><i className="inline-block w-2 h-2 rounded-full mr-1" style={{ background: c }} />{k}</span>)}
                            <span><i className="inline-block w-3 h-1.5 mr-1 bg-black border border-white/30" />heartbeat missed</span>
                        </div>
                    </Card>

                    <Card title="2 · Route recalculated by Dijkstra">
                        <div className="overflow-x-auto">
                            <table className="w-full text-[12px]">
                                <thead><tr className="text-left text-white/40">{['Tick', 'State', 'Action on routing graph', 'Route A→F', 'Cost', 'Route changed'].map(h => <th key={h} className="font-normal py-1 pr-3">{h}</th>)}</tr></thead>
                                <tbody>{res.timeline.map((t, i) => (
                                    <tr key={i} className="border-t border-white/5">
                                        <td className="py-1 pr-3 text-white/50">{t.tick}</td>
                                        <td className="pr-3" style={{ color: STATE_COLOUR[t.state] }}>{t.state}</td>
                                        <td className="pr-3 text-white/60">{t.action}</td>
                                        <td className="pr-3 text-white">{t.path.length ? t.path.join(' → ') : 'unreachable'}</td>
                                        <td className="pr-3 text-white/60">{t.cost ?? '—'}</td>
                                        <td className="text-white/60">{t.rerouted ? 'yes' : 'no'}</td>
                                    </tr>))}</tbody>
                            </table>
                        </div>
                    </Card>

                    <Card title="3 · Same transfer over each route (TCP Reno model)" badge={<Badge kind="simulation" />}>
                        <div className="grid gap-4 md:grid-cols-3">
                            {res.phases.map(ph => (
                                <div key={ph.phase} className="border border-white/10 rounded-md p-3">
                                    <h3 className="text-[12px] text-white">{names[ph.phase] ?? ph.phase}</h3>
                                    <p className="text-[11px] text-white/40 mt-0.5 mb-2">{ph.description}</p>
                                    {ph.reachable && ph.metrics && ph.path_params ? (
                                        <>
                                            <p className="text-[12px] text-white/70 mb-2">{ph.path.join(' → ')}</p>
                                            <div className="grid grid-cols-2 gap-2">
                                                <Metric label="Completion" value={fmt(ph.metrics.completion_time_s)} unit="s" />
                                                <Metric label="Goodput" value={fmt(ph.metrics.goodput_mbps)} unit="Mbit/s" />
                                                <Metric label="Retransmits" value={ph.metrics.retransmissions} />
                                                <Metric label="Timeouts" value={ph.metrics.timeouts} />
                                                <Metric label="RTT" value={fmt(ph.path_params.rtt_ms, 0)} unit="ms" />
                                                <Metric label="Bottleneck" value={ph.path_params.bandwidth_mbps} unit="Mbit/s" />
                                            </div>
                                        </>
                                    ) : <p className="text-[12px] text-red-300">No route to the destination.</p>}
                                </div>
                            ))}
                        </div>
                        <div className="mt-5 grid gap-5 lg:grid-cols-2">
                            <div>
                                <h3 className="text-[11px] tracking-[0.12em] uppercase text-white/40 mb-2">Transfer completion time</h3>
                                <Bars unit="s" items={res.phases.filter(p => p.metrics?.completion_time_s != null).map((p, i) => ({
                                    label: names[p.phase] ?? p.phase, value: p.metrics!.completion_time_s as number, colour: [COLOURS.c, COLOURS.bad, COLOURS.a][i] ?? COLOURS.a }))} />
                            </div>
                            <LineChart xLabel="time (s)" yLabel="cwnd (segments)"
                                series={res.phases.filter(p => p.cwnd_series).map((p, i) => ({
                                    name: names[p.phase] ?? p.phase, colour: [COLOURS.c, COLOURS.bad, COLOURS.a][i] ?? COLOURS.a,
                                    step: true, points: p.cwnd_series!.map(r => [r[0], r[1]] as [number, number]) }))} />
                        </div>
                    </Card>

                    <Card title="Outcome">
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">
                            <Metric label="Deepest state" value={res.outcome.deepest_state} />
                            <Metric label="Route changes (hysteresis)" value={res.outcome.route_changes_hysteresis} />
                            <Metric label="Raw heartbeat toggles" value={res.outcome.heartbeat_toggles_naive} hint="How often a no-threshold detector would have flipped" />
                            <Metric label="Reroute speed-up" value={res.outcome.reroute_speedup_vs_degraded != null ? `${res.outcome.reroute_speedup_vs_degraded}×` : '—'} hint="Degraded completion time ÷ rerouted completion time" />
                        </div>
                        <Notes title="" items={res.outcome.notes} />
                        <div className="mt-4 flex flex-wrap gap-2">
                            <Button variant="ghost" onClick={() => download('netrescue-integration.json', JSON.stringify(res, null, 2), 'application/json')}>Export JSON</Button>
                            <Button variant="ghost" onClick={() => download('netrescue-integration.csv', res.csv, 'text/csv')}>Export CSV (phase metrics)</Button>
                        </div>
                    </Card>
                </>
            )}

            <Notes title="Where the models connect — and where they don't" items={res?.boundaries ?? [
                'Heartbeats here are a generated sequence, not live probes; the live overlay is the separate Recovery lab.',
                'Routing is cost-based and knows nothing about queues; transport reduces a route to RTT, loss and bottleneck bandwidth.',
                'The multipath file-transfer engine is not run here, and a route is a graph path, not a physically independent network path.',
            ]} />
        </LabPage>
    );
}
