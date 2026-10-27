/**
 * TrafficShapingLabPage — Feature G: QoS and traffic-shaping lab.
 *
 * One workload, two shapers. The backend generates a seeded packet-arrival
 * list and runs a token bucket and a leaky bucket over exactly that list; this
 * page plots arrivals against departures, queue occupancy and token level, and
 * tabulates throughput, delay, queue length and drops for both.
 */
import React, { useState } from 'react';
import { Play } from 'lucide-react';
import { labsApi, type ShapeResult } from '../labs/api';
import {
    Badge, Button, Card, COLOURS, ErrorBanner, ExecutionBadge, Field, fmt, LabPage, LineChart, Notes, Select,
    StaleBanner, useAction, useLabResult,
} from '../labs/ui';

interface Form {
    pattern: string; rate_bps: number; bucket_bytes: number; queue_packets: number;
    packet_bytes: number; duration_s: number; arrival_rate_bps: number;
    burst_packets: number; burst_interval_s: number; burst_peak_bps: number; seed: number;
}

const DEFAULT: Form = {
    pattern: 'burst', rate_bps: 1_000_000, bucket_bytes: 6000, queue_packets: 20,
    packet_bytes: 1000, duration_s: 5, arrival_rate_bps: 800_000,
    burst_packets: 15, burst_interval_s: 1, burst_peak_bps: 8_000_000, seed: 7,
};

const SCENARIOS: { label: string; patch: Partial<Form> }[] = [
    { label: 'Steady (below rate)', patch: { pattern: 'steady', arrival_rate_bps: 600_000 } },
    { label: 'Bursts', patch: { pattern: 'burst' } },
    { label: 'Sustained overload', patch: { pattern: 'overload', queue_packets: 15 } },
    { label: 'Random (Poisson)', patch: { pattern: 'poisson', arrival_rate_bps: 900_000 } },
];

const cumulative = (times: number[]): [number, number][] => times.map((t, i) => [t, i + 1]);

export default function TrafficShapingLabPage() {
    const [form, setForm] = useState<Form>(DEFAULT);
    const act = useAction();
    const set = (k: keyof Form) => (v: number) => setForm({ ...form, [k]: v });
    const formKey = JSON.stringify(form);
    const shaped = useLabResult<ShapeResult>(formKey);
    const res = shaped.result;
    const invalid = Object.entries(form).some(([k, v]) => k !== 'pattern' && !Number.isFinite(v as number));
    const run = () => act.run(async () => shaped.set(await labsApi.shaping({ ...form }), formKey));

    const rows: [string, string, (s: Record<string, number>) => string][] = [
        ['Delivered packets', '', s => String(s.delivered_packets)],
        ['Dropped packets', '', s => `${s.dropped_packets} (${fmt(s.drop_rate * 100, 1)}%)`],
        ['Throughput', 'kbit/s', s => fmt(s.throughput_bps / 1000, 1)],
        ['Mean delay', 'ms', s => fmt(s.mean_delay_ms, 2)],
        ['95th-percentile delay', 'ms', s => fmt(s.p95_delay_ms, 2)],
        ['Max delay', 'ms', s => fmt(s.max_delay_ms, 2)],
        ['Peak queue', 'packets', s => String(s.peak_queue_packets)],
        ['Mean queue', 'packets', s => fmt(s.mean_queue_packets, 2)],
        ['Peak output over 100 ms', 'kbit/s', s => fmt(s.peak_output_bps_100ms / 1000, 0)],
    ];

    return (
        <LabPage title="QoS & Traffic-Shaping Lab" experiment="shaping"
            subtitle="Token bucket versus leaky bucket under the same workload. The token bucket allows bursts up to its depth; the leaky bucket emits at a constant rate."
            back={{ to: '/features', label: 'Back to features' }}>
            <ErrorBanner error={act.error} onClose={() => act.setError(null)} />

            <Card title="Configuration" badge={<Badge kind="simulation" />}>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <Select label="Traffic pattern" value={form.pattern} onChange={v => setForm({ ...form, pattern: v })}
                        options={[['steady', 'Steady'], ['burst', 'Bursts'], ['overload', 'Overload'], ['poisson', 'Poisson (random)']]} />
                    <Field label="Token / leak rate (bit/s)" value={form.rate_bps} min={1000} onChange={set('rate_bps')} />
                    <Field label="Bucket depth (bytes)" value={form.bucket_bytes} min={1} onChange={set('bucket_bytes')} hint="Token bucket only; must be at least one packet" />
                    <Field label="Queue capacity (packets)" value={form.queue_packets} min={1} max={5000} onChange={set('queue_packets')} />
                    <Field label="Packet size (bytes)" value={form.packet_bytes} min={1} max={9000} onChange={set('packet_bytes')} />
                    <Field label="Duration (s)" value={form.duration_s} min={0.1} max={120} onChange={set('duration_s')} />
                    {(form.pattern === 'steady' || form.pattern === 'poisson' || form.pattern === 'overload') &&
                        <Field label="Incoming rate (bit/s)" value={form.arrival_rate_bps} min={1000} onChange={set('arrival_rate_bps')}
                            hint={form.pattern === 'overload' ? 'Overload uses at least 2× the shaping rate' : undefined} />}
                    {form.pattern === 'burst' && <>
                        <Field label="Packets per burst" value={form.burst_packets} min={1} onChange={set('burst_packets')} />
                        <Field label="Burst interval (s)" value={form.burst_interval_s} min={0.01} onChange={set('burst_interval_s')} />
                        <Field label="Rate inside burst (bit/s)" value={form.burst_peak_bps} min={1000} onChange={set('burst_peak_bps')} />
                    </>}
                    <Field label="Seed" value={form.seed} min={0} onChange={set('seed')} />
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">
                    {SCENARIOS.map(s => (
                        <button key={s.label} onClick={() => setForm({ ...DEFAULT, ...s.patch })}
                            className="h-7 px-3 rounded-full border border-white/10 text-[11px] text-white/55 hover:text-white hover:border-white/30">{s.label}</button>
                    ))}
                </div>
                <div className="mt-4"><Button onClick={run} disabled={act.loading || invalid}><Play size={12} /> {act.loading ? 'Simulating…' : 'Run both shapers'}</Button></div>
                {invalid && <p role="alert" className="mt-2 text-[12px] text-amber-300">Every field needs a number.</p>}
            </Card>

            {!res && !act.loading && <p className="text-[12px] text-white/45">No result yet — configure the shapers and press “Run both shapers”.</p>}
            {res && (
                <>
                    <StaleBanner stale={shaped.stale} onRerun={invalid ? undefined : run} />
                    <ExecutionBadge execution={res.execution} />
                    <Card title="Workload">
                        <p className="text-[13px] text-white/70">
                            {res.workload.packets} packets offered at an average of {fmt(res.workload.offered_bps / 1000, 0)} kbit/s against a
                            shaping rate of {fmt(res.workload.shaping_rate_bps / 1000, 0)} kbit/s —{' '}
                            <span className={res.workload.overloaded ? 'text-amber-300' : 'text-emerald-300'}>
                                {res.workload.overloaded ? 'overloaded on average' : 'within the rate on average'}
                            </span>.
                        </p>
                    </Card>

                    <div className="grid gap-5 lg:grid-cols-2">
                        <Card title="Arrivals vs departures (cumulative packets)">
                            <LineChart xLabel="time (s)" yLabel="packets"
                                series={[
                                    { name: 'arrivals', colour: 'rgba(255,255,255,0.5)', dashed: true, step: true, points: cumulative(res.workload.arrivals) },
                                    { name: 'token bucket departures', colour: COLOURS.b, step: true, points: cumulative(res.token_bucket.departures.map(d => d[1])) },
                                    { name: 'leaky bucket departures', colour: COLOURS.c, step: true, points: cumulative(res.leaky_bucket.departures.map(d => d[1])) },
                                ]} />
                        </Card>
                        <Card title="Queue occupancy">
                            <LineChart xLabel="time (s)" yLabel="packets buffered"
                                series={[
                                    { name: 'token bucket', colour: COLOURS.b, step: true, points: res.token_bucket.queue_series },
                                    { name: 'leaky bucket', colour: COLOURS.c, step: true, points: res.leaky_bucket.queue_series },
                                ]} />
                        </Card>
                        <Card title="Token availability (token bucket)">
                            <LineChart xLabel="time (s)" yLabel="tokens (bytes)"
                                series={[
                                    { name: 'tokens', colour: COLOURS.b, points: res.token_bucket.token_series },
                                    { name: 'bucket depth', colour: 'rgba(255,255,255,0.3)', dashed: true,
                                      points: [[0, Number(res.params.bucket_bytes)], [res.token_bucket.token_series[res.token_bucket.token_series.length - 1]?.[0] ?? 1, Number(res.params.bucket_bytes)]] },
                                ]} />
                        </Card>
                        <Card title="Per-packet delay">
                            <LineChart xLabel="arrival time (s)" yLabel="delay (ms)"
                                series={[
                                    { name: 'token bucket', colour: COLOURS.b, points: res.token_bucket.departures.map(([a, d]) => [a, (d - a) * 1000]) },
                                    { name: 'leaky bucket', colour: COLOURS.c, points: res.leaky_bucket.departures.map(([a, d]) => [a, (d - a) * 1000]) },
                                ]} />
                        </Card>
                    </div>

                    <Card title="Results (identical workload)">
                        <div className="overflow-x-auto">
                            <table className="w-full text-[12px]">
                                <thead><tr className="text-left text-white/40">
                                    <th className="font-normal py-1 pr-3">Metric</th>
                                    <th className="font-normal pr-3" style={{ color: COLOURS.b }}>Token bucket</th>
                                    <th className="font-normal" style={{ color: COLOURS.c }}>Leaky bucket</th></tr></thead>
                                <tbody>{rows.map(([label, unit, f]) => (
                                    <tr key={label} className="border-t border-white/5">
                                        <td className="py-1 pr-3 text-white/55">{label}{unit && <span className="text-white/60"> ({unit})</span>}</td>
                                        <td className="pr-3 text-white">{f(res.token_bucket.summary)}</td>
                                        <td className="text-white">{f(res.leaky_bucket.summary)}</td>
                                    </tr>))}</tbody>
                            </table>
                        </div>
                        <div className="mt-4"><Notes title="What this run showed" items={res.findings} /></div>
                    </Card>
                </>
            )}

            <Notes title="Model and assumptions" items={res?.assumptions ?? [
                'Fixed packet size; the bucket is metered in bytes and the token bucket starts full.',
                'The token bucket releases a packet instantly when tokens suffice; the leaky bucket releases one packet at a time at exactly the configured rate.',
                'Both use the same tail-drop queue limit and the same seeded arrival list.',
                'This models the algorithms; it does not measure a router.',
            ]} />
        </LabPage>
    );
}
