/**
 * labs/ui.tsx — small shared pieces for the networking labs.
 *
 * Visual language matches the existing diagnostics lab: black ground, hairline
 * white/10 borders, white/45 secondary text, pill buttons. Charts are plain SVG
 * so no charting dependency is added.
 */
import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, ArrowLeft } from 'lucide-react';
import { ApiError, labsApi, type ExecutionInfo, type ExecutionProfile } from './api';
import { JOURNEY, WORKFLOWS } from './workflows';

export type Provenance = 'simulation' | 'measurement';

const spinner = 'inline-block w-3 h-3 rounded-full border border-white/30 border-t-white animate-spin';

/** Compact "how to run this experiment" flow, shown before any explanation. */
export function ExperimentWorkflow({ id }: { id: string }) {
    const wf = WORKFLOWS[id];
    if (!wf) return null;
    return (
        <section aria-label="How to run this experiment" data-testid="workflow"
            className="border border-white/10 rounded-lg bg-white/[0.02] p-4 sm:p-5">
            <h2 className="text-[12px] tracking-[0.15em] uppercase text-white/60 mb-3">How to run this experiment</h2>
            <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                {wf.steps.map((s, i) => (
                    <li key={s.title} className="min-w-0">
                        <div className="text-[11px] text-white/40">Step {i + 1}</div>
                        <div className="text-[13px] text-white">{s.title}</div>
                        <p className="text-[12px] text-white/50 leading-relaxed mt-0.5">{s.text}</p>
                    </li>
                ))}
            </ol>
        </section>
    );
}

const MODE_STYLE: Record<string, { colour: string; label: string; short: string }> = {
    REAL: { colour: '#2dd4bf', label: 'REAL NETWORK OPERATION', short: 'REAL' },
    SIMULATION: { colour: '#f5b54a', label: 'SIMULATION', short: 'SIMULATED' },
    MIXED: { colour: '#a78bfa', label: 'MIXED — real and simulated parts', short: 'MIXED' },
    UNVERIFIED: { colour: '#f87171', label: 'UNVERIFIED', short: 'UNVERIFIED' },
};

export function Badge({ kind, children, full = false }: { kind: Provenance | string; children?: React.ReactNode; full?: boolean }) {
    const key = kind === 'measurement' ? 'REAL' : kind === 'simulation' ? 'SIMULATION' : String(kind);
    const m = MODE_STYLE[key] ?? MODE_STYLE.UNVERIFIED;
    return (
        <span className="inline-flex items-center gap-1.5 h-6 px-2.5 rounded-full border text-[10px] tracking-[0.12em] uppercase whitespace-nowrap"
            data-mode={key} style={{ borderColor: `${m.colour}66`, color: m.colour }}
            title={key === 'REAL' ? 'Performed against the operating system or a real socket on this machine'
                : key === 'SIMULATION' ? 'Produced by a model or algorithm; no corresponding live network operation'
                : undefined}>
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: m.colour }} />
            {children ?? (full ? m.label : m.short)}
        </span>
    );
}

/** Run-level execution mode, as established by the backend for THIS result. */
export function ExecutionBadge({ execution }: { execution?: ExecutionInfo | null }) {
    if (!execution) {
        return (
            <div data-testid="execution" className="text-[12px] text-white/45">
                Execution mode was not reported by the API for this result.
            </div>
        );
    }
    return (
        <details data-testid="execution" data-mode={execution.mode}
            className="border border-white/10 rounded-md px-3 py-2 bg-white/[0.02]">
            <summary className="cursor-pointer flex flex-wrap items-center gap-2 text-[12px] text-white/70">
                <Badge kind={execution.mode} full />
                <span>{execution.title}</span>
                <span className="text-white/60">— details: operation, inputs, evidence</span>
            </summary>
            <dl className="mt-2 grid gap-x-4 gap-y-1 text-[12px] sm:grid-cols-[130px_1fr]">
                <dt className="text-white/40">Operation executed</dt><dd className="text-white/70">{execution.operation}</dd>
                {execution.target && <><dt className="text-white/40">Target</dt><dd className="text-white/70">{execution.target}</dd></>}
                {execution.model && <><dt className="text-white/40">Model / algorithm</dt><dd className="text-white/70">{execution.model}</dd></>}
                {execution.seed !== undefined && <><dt className="text-white/40">Random seed</dt><dd className="text-white/70">{execution.seed}</dd></>}
                {execution.outcome && <><dt className="text-white/40">Outcome</dt><dd className="text-white/70">{execution.outcome}</dd></>}
                {execution.inputs && <><dt className="text-white/40">Inputs</dt>
                    <dd className="text-white/55 break-words font-mono text-[11px]">{JSON.stringify(execution.inputs)}</dd></>}
                <dt className="text-white/40">Verified by backend</dt><dd className="text-white/55">{execution.evidence}</dd>
                <dt className="text-white/40">Implementation</dt><dd className="text-white/55 font-mono text-[11px]">{execution.implementation}</dd>
                {execution.limitations.length > 0 && <><dt className="text-white/40">Limitations</dt>
                    <dd className="text-white/55"><ul className="space-y-0.5">{execution.limitations.map(l => <li key={l}>• {l}</li>)}</ul></dd></>}
                {execution.executed_at && <><dt className="text-white/40">Executed at</dt><dd className="text-white/45">{execution.executed_at}</dd></>}
            </dl>
        </details>
    );
}

let profilePromise: Promise<ExecutionProfile> | null = null;
export function resetExecutionProfileCache() { profilePromise = null; }

/** Feature-level execution mode, read from the backend registry (not hard-coded here). */
export function FeatureModePanel({ feature }: { feature: string }) {
    const [profile, setProfile] = useState<ExecutionProfile | null>(null);
    const [error, setError] = useState<string | null>(null);
    useEffect(() => {
        let alive = true;
        profilePromise = profilePromise ?? labsApi.execution();
        profilePromise.then(p => alive && setProfile(p)).catch(e => {
            profilePromise = null;
            if (alive) setError(e instanceof Error ? e.message : String(e));
        });
        return () => { alive = false; };
    }, []);

    if (error) {
        return <div data-testid="mode-panel" className="text-[12px] text-amber-300/80 border border-amber-500/30 rounded-md px-3 py-2">
            Execution mode unavailable: {error}</div>;
    }
    if (!profile) {
        return <div data-testid="mode-panel" className="text-[12px] text-white/40 flex items-center gap-2"><span className={spinner} /> Checking execution mode…</div>;
    }
    const f = profile.features[feature];
    if (!f) return null;
    const real = f.operations.filter(o => o.mode === 'REAL').length;
    const sim = f.operations.filter(o => o.mode === 'SIMULATION').length;
    return (
        <details data-testid="mode-panel" data-mode={f.mode} open
            className="border border-white/10 rounded-lg bg-white/[0.02] px-4 py-3">
            <summary className="cursor-pointer flex flex-wrap items-center gap-2 text-[12px] text-white/70">
                <span className="tracking-[0.15em] uppercase text-white/60">Execution mode</span>
                <Badge kind={f.mode} full />
                <span className="text-white/40">{real} real · {sim} simulated operation{real + sim === 1 ? '' : 's'}</span>
            </summary>
            <p className="text-[12px] text-white/50 mt-2">
                <strong className="text-white/70">REAL</strong> — {profile.definitions.REAL}{' '}
                <strong className="text-white/70">SIMULATION</strong> — {profile.definitions.SIMULATION}
                {f.mode === 'MIXED' && <> This experiment contains both, so each result is labelled separately.</>}
            </p>
            <ul className="mt-2 grid gap-1.5">
                {f.operations.map(o => (
                    <li key={o.id} className="text-[12px] flex flex-wrap items-baseline gap-2">
                        <Badge kind={o.mode} />
                        <span className="text-white/75">{o.title}</span>
                        <span className="text-white/40">{o.operation}</span>
                    </li>
                ))}
            </ul>
            <p className="text-[11px] text-white/60 mt-2">Classification is verified by the backend against the implementing code, not set by this page.</p>
        </details>
    );
}

/** Where this page sits in the Network Experiment Lab journey. */
export function JourneyBar({ current }: { current: string }) {
    const i = JOURNEY.findIndex(s => s.id === current);
    if (i < 0) return null;
    const prev = JOURNEY[i - 1], next = JOURNEY[i + 1];
    return (
        <nav aria-label="Network Experiment Lab journey" data-testid="journey"
            className="mt-4 flex flex-wrap items-center gap-3">
            <ol className="flex items-center gap-1.5" aria-label={`Step ${i + 1} of ${JOURNEY.length}`}>
                {JOURNEY.map((s, k) => (
                    <li key={s.id} aria-current={k === i ? 'step' : undefined} title={s.label}
                        className={`h-6 min-w-6 px-2 rounded-full border text-[11px] inline-flex items-center justify-center ${
                            k === i ? 'bg-white text-black border-white' : k < i ? 'border-white/40 text-white/70' : 'border-white/10 text-white/60'}`}>
                        {k + 1}
                    </li>
                ))}
            </ol>
            <span className="text-[12px] text-white/60">Step {i + 1} of {JOURNEY.length} · {JOURNEY[i].label}</span>
            <span className="flex gap-2 ml-auto">
                {prev && <Link to={prev.path} className="h-8 px-3 rounded-full border border-white/15 text-[12px] text-white/70 hover:text-white hover:border-white/40 inline-flex items-center">← {prev.label}</Link>}
                {next && <Link to={next.path} className="h-8 px-3 rounded-full bg-white text-black text-[12px] font-medium hover:bg-white/90 inline-flex items-center">Next: {next.label} →</Link>}
            </span>
        </nav>
    );
}

export function LabPage({ title, subtitle, back, experiment, children }: {
    title: string; subtitle: string; back: { to: string; label: string };
    /** Key into WORKFLOWS and the execution profile (e.g. "routing"). */
    experiment: string; children: React.ReactNode;
}) {
    return (
        <main className="pt-20 px-3 sm:px-6 md:px-8 pb-16 max-w-[1400px] mx-auto">
            <header>
                <Link to={back.to}
                    className="inline-flex items-center gap-2 text-[12px] text-white/40 hover:text-white/70 transition-colors">
                    <ArrowLeft size={13} /> {back.label}
                </Link>
                <h1 className="mt-2 font-light tracking-[-0.03em] text-white uppercase"
                    style={{ fontSize: 'clamp(24px, 3.2vw, 36px)' }}>{title}</h1>
                <JourneyBar current={experiment} />
            </header>
            <div className="mt-5 space-y-5">
                <FeatureModePanel feature={experiment} />
                {children}
                <section aria-label="About this experiment" className="border-t border-white/10 pt-4">
                    <h2 className="text-[11px] tracking-[0.15em] uppercase text-white/40 mb-1">About this experiment</h2>
                    <p className="text-[13px] text-white/55 max-w-3xl leading-relaxed">{subtitle}</p>
                </section>
            </div>
        </main>
    );
}

/** Keeps a result together with the exact inputs it was produced from. */
export function useLabResult<T>(currentKey: string) {
    const [state, setState] = useState<{ key: string; value: T } | null>(null);
    return {
        result: state?.value ?? null,
        stale: state !== null && state.key !== currentKey,
        set: (value: T, key: string) => setState({ key, value }),
        clear: () => setState(null),
    };
}

export function StaleBanner({ stale, onRerun }: { stale: boolean; onRerun?: () => void }) {
    if (!stale) return null;
    return (
        <div role="status" data-testid="stale"
            className="flex flex-wrap items-center gap-3 border border-amber-500/40 bg-amber-950/20 rounded-md px-4 py-2 text-[12px] text-amber-200">
            <span className="flex-1">These results were produced from earlier settings. The inputs have changed since — run again to update them.</span>
            {onRerun && <button onClick={onRerun} className="h-7 px-3 rounded-full bg-amber-200 text-black text-[12px] font-medium">Run again</button>}
        </div>
    );
}

/** Run guide + execution mode for pages that are not built on LabPage. */
export function RunGuide({ experiment }: { experiment: string }) {
    return (
        <div className="mt-5 space-y-4" data-testid="run-guide">
            <FeatureModePanel feature={experiment} />
        </div>
    );
}

export function Loading({ what }: { what: string }) {
    return <div role="status" className="text-[12px] text-white/50 flex items-center gap-2"><span className={spinner} />{what}</div>;
}

export function Card({ title, badge, children, className = '' }: {
    title?: string; badge?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
    return (
        <section className={`border border-white/10 rounded-lg bg-white/[0.02] p-4 sm:p-5 ${className}`}>
            {(title || badge) && (
                <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                    {title && <h2 className="text-[12px] tracking-[0.15em] uppercase text-white/60">{title}</h2>}
                    {badge}
                </div>
            )}
            {children}
        </section>
    );
}

export function Metric({ label, value, unit, hint }: {
    label: string; value: React.ReactNode; unit?: string; hint?: string;
}) {
    return (
        <div className="border border-white/10 rounded-md px-3 py-2.5 min-w-0" title={hint}>
            <div className="text-[10px] tracking-[0.12em] uppercase text-white/40 truncate">{label}</div>
            <div className="text-[18px] text-white mt-0.5 truncate">
                {value}{unit && <span className="text-[11px] text-white/40 ml-1">{unit}</span>}
            </div>
        </div>
    );
}

export function Field({ label, value, onChange, min, max, step, hint }: {
    label: string; value: number; onChange: (v: number) => void;
    min?: number; max?: number; step?: number; hint?: string;
}) {
    return (
        <label className="block text-[11px] text-white/50" title={hint}>
            <span className="block mb-1">{label}</span>
            <input type="number" value={Number.isFinite(value) ? value : ''} min={min} max={max} step={step ?? 'any'}
                onChange={e => onChange(e.target.value === '' ? NaN : Number(e.target.value))}
                className="w-full h-9 px-2 rounded-md bg-black border border-white/15 text-white text-[13px] focus:outline-none focus:border-white/40" />
        </label>
    );
}

export function Select({ label, value, onChange, options }: {
    label: string; value: string; onChange: (v: string) => void; options: [string, string][];
}) {
    return (
        <label className="block text-[11px] text-white/50">
            <span className="block mb-1">{label}</span>
            <select value={value} onChange={e => onChange(e.target.value)}
                className="w-full h-9 px-2 rounded-md bg-black border border-white/15 text-white text-[13px] focus:outline-none focus:border-white/40">
                {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
        </label>
    );
}

export function Button({ children, onClick, disabled, variant = 'primary', type = 'button' }: {
    children: React.ReactNode; onClick?: () => void; disabled?: boolean;
    variant?: 'primary' | 'ghost'; type?: 'button' | 'submit';
}) {
    const cls = variant === 'primary'
        ? 'bg-white text-black hover:bg-white/90'
        : 'border border-white/20 text-white/80 hover:border-white/50';
    return (
        <button type={type} onClick={onClick} disabled={disabled}
            className={`h-9 px-4 rounded-full text-[13px] font-medium disabled:opacity-30 transition-colors inline-flex items-center gap-2 ${cls}`}>
            {children}
        </button>
    );
}

export function ErrorBanner({ error, onClose }: { error: string | null; onClose: () => void }) {
    if (!error) return null;
    return (
        <div role="alert"
            className="flex items-start gap-2 border border-red-500/40 bg-red-950/20 rounded-md px-4 py-3 text-[13px] text-red-300">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            <span className="flex-1 break-words">{error}</span>
            <button onClick={onClose} aria-label="Dismiss" className="text-red-300/60 hover:text-red-200">✕</button>
        </div>
    );
}

export function Notes({ title, items }: { title: string; items?: string[] }) {
    if (!items || items.length === 0) return null;
    return (
        <div>
            <h3 className="text-[11px] tracking-[0.12em] uppercase text-white/40 mb-1.5">{title}</h3>
            <ul className="space-y-1">
                {items.map((t, i) => <li key={i} className="text-[12px] text-white/55 leading-relaxed">• {t}</li>)}
            </ul>
        </div>
    );
}

/** Run an async action with loading + error state. */
export function useAction() {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const run = async (fn: () => Promise<void>) => {
        setLoading(true);
        setError(null);
        try { await fn(); }
        catch (e) { setError(e instanceof ApiError ? e.message : String(e)); }
        finally { setLoading(false); }
    };
    return { loading, error, setError, run };
}

export function download(filename: string, text: string, mime: string) {
    const url = URL.createObjectURL(new Blob([text], { type: mime }));
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}

export const fmt = (v: unknown, digits = 2): string =>
    typeof v === 'number' && Number.isFinite(v) ? String(Number(v.toFixed(digits))) : v == null ? '—' : String(v);

// ---------------------------------------------------------------------------
// SVG charts
// ---------------------------------------------------------------------------

export interface Series {
    name: string; colour: string; points: [number, number][]; step?: boolean; dashed?: boolean;
}

const W = 640, H = 220, PAD = { l: 44, r: 12, t: 12, b: 28 };

export function LineChart({ series, xLabel, yLabel, markers = [], yMin = 0 }: {
    series: Series[]; xLabel: string; yLabel: string;
    markers?: { x: number; label: string; colour: string }[]; yMin?: number;
}) {
    const all = series.flatMap(s => s.points);
    if (all.length === 0) return <p className="text-[12px] text-white/40">No data.</p>;
    const xMax = Math.max(...all.map(p => p[0]), 1e-9);
    const yMax = Math.max(...all.map(p => p[1]), yMin + 1e-9) * 1.08;
    const sx = (x: number) => PAD.l + (x / xMax) * (W - PAD.l - PAD.r);
    const sy = (y: number) => H - PAD.b - ((y - yMin) / (yMax - yMin)) * (H - PAD.t - PAD.b);
    const path = (s: Series) => {
        const pts = s.points;
        if (!s.step) return pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join('');
        let d = '';
        pts.forEach((p, i) => {
            d += i ? `L${sx(p[0]).toFixed(1)},${sy(pts[i - 1][1]).toFixed(1)}L${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`
                   : `M${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`;
        });
        return d;
    };
    const ticks = [0, 0.25, 0.5, 0.75, 1];
    return (
        <div>
            <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img" aria-label={`${yLabel} over ${xLabel}`}>
                {ticks.map(t => {
                    const y = yMin + t * (yMax - yMin);
                    return (
                        <g key={t}>
                            <line x1={PAD.l} x2={W - PAD.r} y1={sy(y)} y2={sy(y)} stroke="rgba(255,255,255,0.07)" />
                            <text x={PAD.l - 6} y={sy(y) + 3} textAnchor="end" fontSize="9" fill="rgba(255,255,255,0.4)">{fmt(y, y < 10 ? 1 : 0)}</text>
                            <text x={sx(t * xMax)} y={H - 10} textAnchor="middle" fontSize="9" fill="rgba(255,255,255,0.4)">{fmt(t * xMax, xMax < 10 ? 2 : 0)}</text>
                        </g>
                    );
                })}
                {markers.map((m, i) => (
                    <g key={i}>
                        <line x1={sx(m.x)} x2={sx(m.x)} y1={PAD.t} y2={H - PAD.b} stroke={m.colour} strokeDasharray="2 3" opacity="0.7" />
                    </g>
                ))}
                {series.map(s => (
                    <path key={s.name} d={path(s)} fill="none" stroke={s.colour} strokeWidth="1.6"
                        strokeDasharray={s.dashed ? '4 3' : undefined} strokeLinejoin="round" />
                ))}
                <text x={PAD.l} y={9} fontSize="9" fill="rgba(255,255,255,0.45)">{yLabel}</text>
                <text x={W - PAD.r} y={H - 1} textAnchor="end" fontSize="9" fill="rgba(255,255,255,0.45)">{xLabel}</text>
            </svg>
            <div className="flex flex-wrap gap-x-4 gap-y-1 mt-1">
                {series.map(s => (
                    <span key={s.name} className="text-[11px] text-white/55 inline-flex items-center gap-1.5">
                        <span className="w-3 h-0.5 inline-block" style={{ background: s.colour }} />{s.name}
                    </span>
                ))}
                {markers.map((m, i) => (
                    <span key={i} className="text-[11px] text-white/55 inline-flex items-center gap-1.5">
                        <span className="w-0.5 h-3 inline-block" style={{ background: m.colour }} />{m.label}
                    </span>
                ))}
            </div>
        </div>
    );
}

/** Horizontal bar comparison for a handful of labelled values. */
export function Bars({ items, unit }: { items: { label: string; value: number; colour: string }[]; unit: string }) {
    const max = Math.max(...items.map(i => i.value), 1e-9);
    return (
        <div className="space-y-2">
            {items.map(i => (
                <div key={i.label}>
                    <div className="flex justify-between text-[11px] text-white/55">
                        <span>{i.label}</span><span>{fmt(i.value, 2)} {unit}</span>
                    </div>
                    <div className="h-2 rounded-full bg-white/[0.06] overflow-hidden">
                        <div className="h-full rounded-full" style={{ width: `${(i.value / max) * 100}%`, background: i.colour }} />
                    </div>
                </div>
            ))}
        </div>
    );
}

export const COLOURS = { a: '#7dd3fc', b: '#f5b54a', c: '#2dd4bf', d: '#f472b6', e: '#a78bfa', bad: '#f87171' };
