/**
 * TcpUdpLabPage — Feature H: TCP vs UDP performance lab.
 *
 * Two clearly separate modes that are never mixed in one table:
 *   - Real loopback: actual sockets on 127.0.0.1, measured on this machine.
 *   - Simulated: the Reno model versus a paced UDP sender over a modelled
 *     lossy bottleneck.
 * The mode badge, the method and the limitations come from the API response.
 */
import React, { useState } from 'react';
import LocalOnlyNote from '../components/LocalOnlyNote';
import { Play } from 'lucide-react';
import { labsApi, type TcpUdpResult } from '../labs/api';
import {
    Badge, Button, Card, COLOURS, download, ErrorBanner, ExecutionBadge, Field, fmt, LabPage, LineChart, Metric, Notes,
    StaleBanner, useAction, useLabResult,
} from '../labs/ui';

export default function TcpUdpLabPage() {
    const [mode, setMode] = useState<'real' | 'sim'>('real');
    const [real, setReal] = useState({ ping_count: 200, payload_bytes: 512, bulk_bytes: 8_000_000, udp_datagrams: 5000, udp_datagram_bytes: 1000 });
    const [sim, setSim] = useState({ rtt_ms: 60, bandwidth_mbps: 10, loss_rate: 0.01, buffer_packets: 40, transfer_bytes: 2_000_000, udp_send_rate_mbps: 8, seed: 11 });
    const act = useAction();
    const realRes = useLabResult<TcpUdpResult>(JSON.stringify(real));
    const simRes = useLabResult<TcpUdpResult>(JSON.stringify(sim));
    const cur = mode === 'real' ? realRes : simRes;

    const execute = () => act.run(async () => {
        if (mode === 'real') {
            realRes.set(await labsApi.tcpUdpReal({ ...real }), JSON.stringify(real));
        } else {
            simRes.set(await labsApi.tcpUdpSim({ ...sim }), JSON.stringify(sim));
        }
    });

    const res = cur.result;
    const setR = (k: keyof typeof real) => (v: number) => setReal({ ...real, [k]: v });
    const setS = (k: keyof typeof sim) => (v: number) => setSim({ ...sim, [k]: v });

    return (
        <LabPage title="TCP vs UDP Performance Lab" experiment="tcp-udp"
            subtitle="Compare connection setup, reliability, ordering and delivery. Real-socket measurements and simulations are kept strictly apart and labelled."
            back={{ to: '/features', label: 'Back to features' }}>
            <ErrorBanner error={act.error} onClose={() => act.setError(null)} />

            <Card title="Protocol differences">
                <div className="overflow-x-auto">
                    <table className="w-full text-[12px]">
                        <thead><tr className="text-left text-white/40"><th className="font-normal py-1 pr-3">Aspect</th><th className="font-normal pr-3" style={{ color: COLOURS.a }}>TCP</th><th className="font-normal" style={{ color: COLOURS.b }}>UDP</th></tr></thead>
                        <tbody>
                            {(res?.protocol_notes ?? DEFAULT_NOTES).map(n => (
                                <tr key={n.aspect} className="border-t border-white/5 align-top">
                                    <td className="py-1.5 pr-3 text-white/70 whitespace-nowrap">{n.aspect}</td>
                                    <td className="pr-3 text-white/55 min-w-[180px]">{n.tcp}</td>
                                    <td className="text-white/55 min-w-[180px]">{n.udp}</td>
                                </tr>))}
                        </tbody>
                    </table>
                </div>
            </Card>

            <Card title="Experiment">
                <div role="tablist" aria-label="Experiment mode" className="inline-flex rounded-full border border-white/15 p-0.5 mb-4">
                    {([['real', 'Real loopback sockets'], ['sim', 'Simulation']] as const).map(([k, label]) => (
                        <button key={k} role="tab" aria-selected={mode === k} onClick={() => setMode(k)}
                            className={`h-8 px-4 rounded-full text-[12px] transition-colors ${mode === k ? 'bg-white text-black' : 'text-white/60 hover:text-white'}`}>{label}</button>
                    ))}
                </div>
                {mode === 'real' ? (
                    <>
                        <p className="text-[12px] text-white/50 mb-3">
                            Runs real TCP and UDP sockets on 127.0.0.1 inside the API process. Loopback has no
                            latency, bandwidth limit or normal packet loss, so this measures this machine's stack — not a network.
                        </p>
                        <LocalOnlyNote>
                            On this hosted site the sockets would run on the Render server, not on your device, so this
                            experiment is disabled for public visitors. Run NetRescue locally to measure your own machine,
                            or use the Simulation tab.
                        </LocalOnlyNote>
                        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mt-3">
                            <Field label="Ping round trips" value={real.ping_count} min={10} max={2000} onChange={setR('ping_count')} />
                            <Field label="Ping payload (bytes)" value={real.payload_bytes} min={8} max={1400} onChange={setR('payload_bytes')} />
                            <Field label="TCP bulk (bytes)" value={real.bulk_bytes} min={100000} max={64000000} onChange={setR('bulk_bytes')} />
                            <Field label="UDP datagrams" value={real.udp_datagrams} min={100} max={50000} onChange={setR('udp_datagrams')} />
                            <Field label="UDP datagram (bytes)" value={real.udp_datagram_bytes} min={8} max={1400} onChange={setR('udp_datagram_bytes')} />
                        </div>
                    </>
                ) : (
                    <>
                        <p className="text-[12px] text-white/50 mb-3">
                            A model: TCP Reno and a paced UDP sender cross the same modelled bottleneck (one at a time). No sockets are used.
                        </p>
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                            <Field label="RTT (ms)" value={sim.rtt_ms} min={0.1} max={2000} onChange={setS('rtt_ms')} />
                            <Field label="Bandwidth (Mbit/s)" value={sim.bandwidth_mbps} min={0.1} max={100} onChange={setS('bandwidth_mbps')} />
                            <Field label="Random loss" value={sim.loss_rate} min={0} max={0.49} step={0.005} onChange={setS('loss_rate')} />
                            <Field label="Buffer (packets)" value={sim.buffer_packets} min={1} onChange={setS('buffer_packets')} />
                            <Field label="Transfer (bytes)" value={sim.transfer_bytes} min={10000} max={50000000} onChange={setS('transfer_bytes')} />
                            <Field label="UDP send rate (Mbit/s)" value={sim.udp_send_rate_mbps} min={1} max={100} onChange={setS('udp_send_rate_mbps')} hint="UDP has no congestion control: the sender chooses its rate" />
                            <Field label="Seed" value={sim.seed} min={0} onChange={setS('seed')} />
                        </div>
                    </>
                )}
                <div className="mt-4 flex gap-2">
                    <Button onClick={execute} disabled={act.loading}><Play size={12} /> {act.loading ? 'Running…' : mode === 'real' ? 'Run real experiment' : 'Run simulation'}</Button>
                    {res && <Button variant="ghost" onClick={() => download(`tcp-udp-${res.mode}.json`, JSON.stringify(res, null, 2), 'application/json')}>Export JSON</Button>}
                </div>
            </Card>

            {!res && !act.loading && <p className="text-[12px] text-white/45">No {mode === 'real' ? 'real-socket' : 'simulation'} result yet — configure it and press Run.</p>}
            {res && (
                <>
                    <StaleBanner stale={cur.stale} onRerun={execute} />
                    <ExecutionBadge execution={res.execution} />
                    <div className="flex flex-wrap items-center gap-3">
                        <Badge kind={res.execution?.mode ?? 'UNVERIFIED'} />
                        <span className="text-[12px] text-white/55">{res.label}</span>
                        {res.run_duration_s != null && <span className="text-[12px] text-white/60">ran for {res.run_duration_s} s</span>}
                    </div>
                    {res.mode === 'real_loopback' ? <RealView r={res} /> : <SimView r={res} />}
                    <Card title="What this run showed"><Notes title="" items={res.findings} /></Card>
                    <div className="grid gap-5 md:grid-cols-2">
                        <Notes title={res.method ? 'Method' : 'Assumptions'} items={res.method ?? res.assumptions} />
                        <Notes title="Limitations" items={res.limitations} />
                    </div>
                </>
            )}
        </LabPage>
    );
}

const DEFAULT_NOTES = [
    { aspect: 'Connection setup', tcp: 'Three-way handshake before data (one extra round trip).', udp: 'None.' },
    { aspect: 'Reliability', tcp: 'Lost segments are retransmitted until acknowledged.', udp: 'None; a lost datagram is gone unless the application resends it.' },
    { aspect: 'Ordering', tcp: 'In-order byte stream.', udp: 'Independent datagrams; may be reordered, duplicated or lost.' },
    { aspect: 'Congestion control', tcp: 'Slows down when it infers congestion.', udp: 'None built in.' },
];

function RealView({ r }: { r: TcpUdpResult }) {
    const tl = r.tcp.latency, ul = r.udp.latency, tb = r.tcp.bulk, ub = r.udp.bulk;
    return (
        <div className="grid gap-5 lg:grid-cols-2">
            <Card title="TCP" badge={<Badge kind="measurement" />}>
                <div className="grid grid-cols-2 gap-2">
                    <Metric label="connect()" value={fmt(r.tcp.connect_ms, 3)} unit="ms" />
                    <Metric label="Median RTT" value={fmt(tl?.p50_ms, 3)} unit="ms" />
                    <Metric label="95th-pct RTT" value={fmt(tl?.p95_ms, 3)} unit="ms" />
                    <Metric label="Bulk throughput" value={fmt(tb?.throughput_mbps, 0)} unit="Mbit/s" />
                    <Metric label="Delivered" value={tb ? `${fmt(tb.delivered_ratio * 100, 1)}%` : '—'} />
                    <Metric label="Round trips" value={tl?.n ?? '—'} />
                </div>
                {r.tcp.errors?.length > 0 && <p className="mt-2 text-[12px] text-red-300">{r.tcp.errors.join('; ')}</p>}
            </Card>
            <Card title="UDP" badge={<Badge kind="measurement" />}>
                <div className="grid grid-cols-2 gap-2">
                    <Metric label="Setup" value="none" />
                    <Metric label="Median RTT" value={fmt(ul?.p50_ms, 3)} unit="ms" />
                    <Metric label="95th-pct RTT" value={fmt(ul?.p95_ms, 3)} unit="ms" />
                    <Metric label="Send rate" value={fmt(ub?.send_rate_mbps, 0)} unit="Mbit/s" />
                    <Metric label="Delivered" value={ub ? `${fmt(ub.delivered_ratio * 100, 2)}%` : '—'} />
                    <Metric label="Reordered / dup." value={ub ? `${ub.reordered} / ${ub.duplicates}` : '—'} />
                    <Metric label="Ping timeouts" value={ul?.timeouts ?? '—'} />
                </div>
                {r.udp.errors?.length > 0 && <p className="mt-2 text-[12px] text-red-300">{r.udp.errors.join('; ')}</p>}
            </Card>
            {r.environment && <p className="lg:col-span-2 text-[11px] text-white/60">Environment: {r.environment.platform}, Python {r.environment.python}</p>}
        </div>
    );
}

function SimView({ r }: { r: TcpUdpResult }) {
    const t = r.tcp, u = r.udp;
    return (
        <div className="space-y-5">
            <div className="grid gap-5 lg:grid-cols-2">
                <Card title="TCP (Reno model)" badge={<Badge kind="simulation" />}>
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                        <Metric label="Handshake" value={fmt(t.handshake_ms, 0)} unit="ms" />
                        <Metric label="Completion" value={t.completed ? fmt(t.completion_time_s, 2) : 'not finished'} unit={t.completed ? 's' : ''} />
                        <Metric label="Delivered" value={`${fmt(t.delivered_ratio * 100, 1)}%`} />
                        <Metric label="Goodput" value={fmt(t.goodput_mbps)} unit="Mbit/s" />
                        <Metric label="Retransmissions" value={t.retransmissions} />
                        <Metric label="Timeouts" value={t.timeouts} />
                    </div>
                </Card>
                <Card title="UDP (paced sender)" badge={<Badge kind="simulation" />}>
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                        <Metric label="Setup" value="none" />
                        <Metric label="Last arrival" value={fmt(u.completion_time_s, 2)} unit="s" />
                        <Metric label="Delivered" value={`${fmt(u.delivered_ratio * 100, 2)}%`} />
                        <Metric label="Goodput" value={fmt(u.goodput_mbps)} unit="Mbit/s" />
                        <Metric label="Lost (queue / random)" value={`${u.queue_drops} / ${u.random_drops}`} />
                        <Metric label="Mean one-way delay" value={fmt(u.one_way_delay_mean_ms, 1)} unit="ms" />
                    </div>
                </Card>
            </div>
            {r.cwnd_series && (
                <Card title="TCP congestion window during this transfer" badge={<Badge kind="simulation" />}>
                    <LineChart xLabel="time (s)" yLabel="cwnd (segments)"
                        series={[{ name: 'cwnd', colour: COLOURS.a, step: true, points: r.cwnd_series.map(x => [x[0], x[1]]) }]} />
                </Card>
            )}
        </div>
    );
}
