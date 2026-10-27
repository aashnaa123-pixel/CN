/**
 * DiagnosticsPlaygroundPage — Feature D as a measurement laboratory.
 *
 * Measure this machine's real network, replay a seeded fault timeline over it,
 * sweep the hysteresis dead band, and rank scheduler variants — every panel
 * labelled with where its numbers came from.
 *
 * The rule that shapes every panel here: a number is never dressed up as
 * something it is not. A ping is REAL because Windows answered it just now.
 * A sweep is CONTROLLED because it replays one identical seeded sequence. A
 * fault timeline is SIMULATED because nothing left this process. `tc`/`netem`
 * is listed as unavailable because it is — this lab never claims to shape the
 * kernel's packet path, because it does not.
 *
 * Features A and C each own a playground reachable from their detail page;
 * this is the third, and it is where the "Run Diagnostics" action lives now
 * rather than on the feature page itself.
 */
import React, { useCallback, useState } from 'react';
import { RunGuide } from '../labs/ui';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { AlertTriangle, ArrowLeft, Play } from 'lucide-react';
import { diagnosticsApi, ApiError } from '../diagnostics/api';
import type { DiagnosticsRun } from '../diagnostics/types';
import DiagnosticsLab, { LIVE_PROBE_BLOCKED } from '../diagnostics/DiagnosticsLab';
import { IS_REMOTE_API } from '../api/client';

export default function DiagnosticsPlaygroundPage() {
    const [run, setRun] = useState<DiagnosticsRun | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const runDiagnostics = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const result = await diagnosticsApi.run({
                include_traceroute: true,
                include_adapters: true,
            });
            setRun(result);
        } catch (e) {
            setError(e instanceof ApiError ? e.message : String(e));
        } finally {
            setLoading(false);
        }
    }, []);

    return (
        <main className="pt-20 px-3 sm:px-6 md:px-8 pb-16">
            <LabHeader
                onRun={runDiagnostics}
                loading={loading}
                run={run}
            />
            <RunGuide experiment="diagnostics" />

            {error && (
                <div role="alert"
                    className="mt-4 flex items-start gap-2 border border-red-500/40 bg-red-950/20 rounded-md px-4 py-3 text-[13px] text-red-300">
                    <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                    <span className="flex-1">{error}</span>
                    <button onClick={() => setError(null)} className="text-red-300/60 hover:text-red-200">✕</button>
                </div>
            )}

            <div className="mt-5">
                <DiagnosticsLab
                    run={run}
                    loading={loading}
                    error={null}
                    onRerun={runDiagnostics}
                />
            </div>
        </main>
    );
}

function LabHeader({
    onRun, loading, run,
}: {
    onRun: () => void;
    loading: boolean;
    run: DiagnosticsRun | null;
}) {
    return (
        <motion.header
            initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}
            className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between"
        >
            <div>
                <Link to="/features/feature-d"
                    className="inline-flex items-center gap-2 text-[12px] text-white/40 hover:text-white/70 transition-colors">
                    <ArrowLeft size={13} /> Back to Network Diagnostics
                </Link>
                <h1 className="mt-2 font-light tracking-[-0.03em] text-white"
                    style={{ fontSize: 'clamp(24px, 3.2vw, 36px)' }}>
                    NETWORK DIAGNOSTICS LAB
                </h1>
                <p className="text-white/45 text-[13px] sm:text-[14px] mt-1">
                    Measure. Replay. Sweep. Compare.
                </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
                <LastRunPill run={run} loading={loading} />
                <button
                    onClick={onRun}
                    disabled={loading || IS_REMOTE_API}
                    title={IS_REMOTE_API ? LIVE_PROBE_BLOCKED : "Run real probes against this machine's network"}
                    className="h-9 px-4 rounded-full bg-white text-black text-[13px] font-medium hover:bg-white/90 disabled:opacity-30 transition-colors flex items-center gap-2"
                >
                    <Play size={12} /> {loading ? 'Running…' : 'Run Diagnostics'}
                </button>
            </div>
        </motion.header>
    );
}

/**
 * What the last run returned, and nothing more. A section that failed is
 * named as failed rather than being hidden, so the count of 3-of-3 is a
 * statement about the backend rather than about this component.
 */
function LastRunPill({ run, loading }: { run: DiagnosticsRun | null; loading: boolean }) {
    if (loading) {
        return (
            <div className="flex items-center gap-2 h-9 px-4 rounded-full border border-white/10 bg-white/[0.03]">
                <span className="text-[12px] text-white/60">Probes in flight…</span>
            </div>
        );
    }
    if (!run) {
        return (
            <div className="flex items-center gap-2 h-9 px-4 rounded-full border border-white/10 bg-white/[0.03]">
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'rgba(255,255,255,0.3)' }} />
                <span className="text-[12px] text-white/60">No run yet</span>
            </div>
        );
    }

    const ok = run.ok_sections?.length ?? 0;
    const bad = run.error_sections?.length ?? 0;
    const colour = bad > 0 ? '#f5b54a' : '#2dd4bf';
    const label = `${ok}/${ok + bad} sections measured`;

    return (
        <div className="flex items-center gap-2 h-9 px-4 rounded-full border border-white/10 bg-white/[0.03]"
            title={`run ${run.run_id} · config ${run.config_hash.slice(0, 12)}…`}>
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: colour }} />
            <span className="text-[12px] text-white/60">{label}</span>
        </div>
    );
}