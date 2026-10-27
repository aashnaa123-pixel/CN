/**
 * MultipathPlaygroundPage — Feature B as a transfer laboratory.
 *
 * Pick a real file. Watch it become chunks on logical paths. Break a path or lose
 * packets. Reconstruct the file and check its SHA-256 against the original.
 *
 * The rule that shapes every panel: nothing here claims something the backend
 * did not report. A chunk is delivered because the transfer session emitted
 * `chunk.delivered`. A file is verified because the two hashes the server
 * computed matched — never because a progress bar reached 100%. Where a number
 * is not measured, the field is absent rather than filled in: there is no
 * latency figure for a chunk that travelled zero real distance, and no
 * wire-throughput claim for a path that is a model rather than a cable.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RunGuide } from '../labs/ui';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
    AlertTriangle, ArrowLeft, CheckCircle2, Download, Play, RefreshCw, Upload, X,
} from 'lucide-react';
import TransferCanvas from '../multipath/TransferCanvas';
import {
    ApiError, downloadBlob, downloadText, fileToBase64, xferApi,
} from '../multipath/api';
import type {
    LabCapabilities, LabEvent, LabState, PathInput,
    SchedulerComparison, TransferState, TransferView, VariantComparison,
} from '../multipath/types';

/** The starting configuration: three heterogeneous logical paths. */
const EXAMPLE_PATHS: PathInput[] = [
    { path_id: 'path-1', fd_estimate_ms: 8, tp_estimate_bps: 8_000_000, loss_rate: 0 },
    { path_id: 'path-2', fd_estimate_ms: 40, tp_estimate_bps: 3_000_000, loss_rate: 0 },
    { path_id: 'path-3', fd_estimate_ms: 120, tp_estimate_bps: 1_000_000, loss_rate: 0 },
];

const CHUNK_PRESETS = [
    { label: '4 KiB', value: 4096 },
    { label: '16 KiB', value: 16384 },
    { label: '64 KiB', value: 65536 },
];

export default function MultipathPlaygroundPage() {
    const [state, setState] = useState<LabState | null>(null);
    const [caps, setCaps] = useState<LabCapabilities | null>(null);
    const [backendUp, setBackendUp] = useState<'checking' | 'up' | 'down'>('checking');
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);

    // Local configuration. Only pushed to the backend when a transfer starts.
    const [file, setFile] = useState<File | null>(null);
    const [paths, setPaths] = useState<PathInput[]>(EXAMPLE_PATHS);
    const [chunkSize, setChunkSize] = useState(4096);
    const [variant, setVariant] = useState('DB-DSP');
    const [policy, setPolicy] = useState('RTX-CL');
    const [maxRetries, setMaxRetries] = useState(3);

    const [sched, setSched] = useState<SchedulerComparison | null>(null);
    const [varCmp, setVarCmp] = useState<VariantComparison | null>(null);

    useEffect(() => {
        document.title = 'Multipath Transfer Lab — NetRescue';
    }, []);

    const refresh = useCallback(async () => {
        try {
            setState(await xferApi.state());
            setBackendUp('up');
            setError(null);
        } catch (exc) {
            setBackendUp('down');
            setError(message(exc));
        }
    }, []);

    useEffect(() => {
        refresh();
        xferApi.capabilities()
            .then(c => {
                setCaps(c);
                setBackendUp('up');
                setVariant(v => (c.scheduler_variants.some(o => o.id === v) ? v : c.scheduler_variants[0]?.id ?? v));
                setPolicy(p => (c.retransmit_policies.some(o => o.id === p) ? p : c.retransmit_policies[0]?.id ?? p));
            })
            .catch(() => setBackendUp('down'));

        // Poll while a transfer is running; idle otherwise so the page is cheap.
        const timer = window.setInterval(() => {
            if (document.visibilityState === 'visible') refresh();
        }, 700);
        return () => window.clearInterval(timer);
    }, [refresh]);

    const transfer = state?.transfer ?? null;
    const running = state?.running ?? false;

    const loadExample = useCallback(() => {
        setPaths(EXAMPLE_PATHS.map(p => ({ ...p })));
        setChunkSize(4096);
        setMaxRetries(3);
        setVariant('DB-DSP');
        setPolicy('RTX-CL');
        setNotice('Example configuration loaded: three logical paths, 4 KiB chunks, DB-DSP with RTX-CL.');
    }, []);

    const doUpload = useCallback(async () => {
        if (!file) return;
        setBusy('upload');
        try {
            const b64 = await fileToBase64(file);
            const next = await xferApi.upload({
                filename: file.name,
                data_b64: b64,
                chunk_size_bytes: chunkSize,
                paths,
                scheduler_variant: variant,
                retransmit_policy: policy,
                max_retries: maxRetries,
            });
            setState(next);
            setError(null);
            setNotice(
                `${file.name} accepted by the backend — ${next.transfer?.total_chunks} chunk(s), ` +
                `source SHA-256 ${next.transfer?.source_sha256.slice(0, 16)}…`,
            );
        } catch (exc) {
            setError(message(exc));
        } finally {
            setBusy(null);
        }
    }, [file, chunkSize, paths, variant, policy, maxRetries]);

    const doStart = useCallback(async () => {
        setBusy('start');
        try {
            const next = await xferApi.start({
                scheduler_variant: variant,
                retransmit_policy: policy,
                max_retries: maxRetries,
                paths,
            });
            setState(next);
            setError(null);
            setNotice('Transfer started. Chunk progress below is read from the backend.');
        } catch (exc) {
            setError(message(exc));
        } finally {
            setBusy(null);
        }
    }, [variant, policy, maxRetries, paths]);

    const doReset = useCallback(async () => {
        if (!window.confirm('Reset the experiment? The uploaded file and all transfer state will be dropped.')) return;
        setFile(null);
        setState(null);
        try {
            setState(await xferApi.reset());
            setNotice('Experiment reset. The uploaded bytes were dropped.');
            setError(null);
        } catch (exc) {
            setError(message(exc));
        }
    }, []);

    const doDownload = useCallback(async () => {
        setBusy('download');
        try {
            const file = await xferApi.download();
            downloadBlob(file.blob, file.filename);
            setNotice(
                `Downloaded ${file.filename} — source ${file.sourceSha256.slice(0, 16)}… ` +
                `reconstructed ${file.reconstructedSha256.slice(0, 16)}…`,
            );
        } catch (exc) {
            setError(message(exc));
        } finally {
            setBusy(null);
        }
    }, []);

    const transfer_ = transfer;

    return (
        <main className="pt-20 px-3 sm:px-6 md:px-8 pb-16">
            <LabHeader
                onReset={doReset}
                busy={busy}
                state={transfer_ ?? null}
                running={running}
                backend={backendUp}
            />
            <RunGuide experiment="multipath" />

            {error && (
                <div role="alert"
                    className="mt-4 flex items-start gap-2 border border-red-500/40 bg-red-950/20 rounded-md px-4 py-3 text-[13px] text-red-300">
                    <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                    <span className="flex-1">{error}</span>
                    <button onClick={() => setError(null)} className="text-red-300/60 hover:text-red-200">✕</button>
                </div>
            )}
            {notice && (
                <div className="mt-4 border border-white/10 bg-white/[0.03] rounded-md px-4 py-3 text-[13px] text-white/70">
                    {notice}
                </div>
            )}

            <div className="mt-5 grid grid-cols-1 xl:grid-cols-[320px_minmax(0,1fr)_340px] gap-4">
                <SetupPanel
                    file={file}
                    setFile={setFile}
                    paths={paths}
                    setPaths={setPaths}
                    chunkSize={chunkSize}
                    setChunkSize={setChunkSize}
                    variant={variant}
                    setVariant={setVariant}
                    policy={policy}
                    setPolicy={setPolicy}
                    maxRetries={maxRetries}
                    setMaxRetries={setMaxRetries}
                    caps={caps}
                    transfer={transfer_ ?? null}
                    running={running}
                    busy={busy}
                    onUpload={doUpload}
                    onStart={doStart}
                    onCancel={() => xferApi.cancel().then(setState).catch(e => setError(message(e)))}
                    onLoadExample={loadExample}
                />

                <section className="order-first xl:order-none border border-white/10 rounded-lg overflow-hidden bg-black/40">
                    <PanelBar title="Transfer network"
                        right={transfer_ ? `${transfer_.scheduler_variant} · ${transfer_.retransmit_policy}` : 'NO TRANSFER'}
                        rightTone={running ? '#ffffff' : 'rgba(255,255,255,0.3)'} />
                    <div className="h-[400px] xl:h-[560px]">
                        <TransferCanvas
                            paths={transfer_?.paths ?? []}
                            chunks={state?.chunks ?? []}
                            running={running}
                            transferId={transfer_?.transfer_id ?? null}
                        />
                    </div>
                    <DeliveryModelBar state={state} />
                </section>

                <PerformancePanel
                    state={state}
                    onDownload={doDownload}
                    downloadAvailable={state?.download_available ?? false}
                    busy={busy}
                />
            </div>

            <div className="mt-4 grid grid-cols-1 lg:grid-cols-3 gap-4 items-start">
                <EventTimeline
                    events={state?.events ?? []}
                    onClear={() => xferApi.clearEvents().then(setState).catch(e => setError(message(e)))}
                    onExport={async fmt => {
                        try {
                            const file = await xferApi.exportLog(fmt);
                            downloadText(file.filename, file.content,
                                fmt === 'csv' ? 'text/csv' : 'application/json');
                        } catch (exc) {
                            setError(message(exc));
                        }
                    }}
                />
                <SchedulerPanel
                    result={sched}
                    setResult={setSched}
                    onError={setError}
                />
                <VariantPanel
                    result={varCmp}
                    setResult={setVarCmp}
                    paths={paths}
                    onError={setError}
                />
            </div>
        </main>
    );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function LabHeader({
    onReset, busy, state, running, backend,
}: {
    onReset: () => void;
    busy: string | null;
    state: import('../multipath/types').TransferView | null;
    running: boolean;
    backend: 'checking' | 'up' | 'down';
}) {
    return (
        <motion.header
            initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}
            className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between"
        >
            <div>
                <Link to="/features/feature-c"
                    className="inline-flex items-center gap-2 text-[12px] text-white/40 hover:text-white/70 transition-colors">
                    <ArrowLeft size={13} /> Back to Multipath Transfer
                </Link>
                <h1 className="mt-2 font-light tracking-[-0.03em] text-white"
                    style={{ fontSize: 'clamp(24px, 3.2vw, 36px)' }}>
                    MULTIPATH TRANSFER LAB
                </h1>
                <p className="text-white/45 text-[13px] sm:text-[14px] mt-1">
                    Transfer. Congest. Compare. Verify.
                </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
                <ConnectionPill backend={backend} />
                <StatusPill state={state} running={running} />
                <button
                    onClick={onReset} disabled={busy !== null}
                    className="h-9 px-4 rounded-full border border-white/15 text-[13px] text-white/70 hover:border-white/40 hover:text-white disabled:opacity-40 transition-colors"
                >
                    Reset Experiment
                </button>
            </div>
        </motion.header>
    );
}

function ConnectionPill({ backend }: { backend: 'checking' | 'up' | 'down' }) {
    const colour = backend === 'up' ? '#2dd4bf' : backend === 'down' ? '#ef4444' : '#f5b54a';
    const text = backend === 'up' ? 'API connected' : backend === 'down' ? 'API unreachable' : 'Checking API';
    return (
        <div className="flex items-center gap-2 h-9 px-4 rounded-full border border-white/10 bg-white/[0.03]">
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: colour }} />
            <span className="text-[12px] text-white/60">{text}</span>
        </div>
    );
}

function StatusPill({ state, running }: {
    state: import('../multipath/types').TransferView | null; running: boolean;
}) {
    const map: Record<string, { label: string; colour: string }> = {
        EMPTY: { label: 'No file loaded', colour: 'rgba(255,255,255,0.3)' },
        READY: { label: 'Ready to start', colour: '#f5b54a' },
        RUNNING: { label: 'Transferring', colour: '#ffffff' },
        COMPLETE: { label: 'Verified', colour: '#2dd4bf' },
        FAILED: { label: 'Failed', colour: '#ef4444' },
        CANCELLED: { label: 'Cancelled', colour: '#ef4444' },
    };
    const current = (state?.state ?? 'EMPTY') as TransferState;
    const item = map[current] ?? map.EMPTY;
    return (
        <div className="flex items-center gap-2 h-9 px-4 rounded-full border border-white/10 bg-white/[0.03]">
            <span className="w-1.5 h-1.5 rounded-full"
                style={{ background: running ? '#ffffff' : item.colour }} />
            <span className="text-[12px] text-white/60">{item.label}</span>
        </div>
    );
}

// ---------------------------------------------------------------------------
// Setup panel
// ---------------------------------------------------------------------------

function FilePicker({
    file, setFile, caps, busy, onUpload,
}: {
    file: File | null;
    setFile: (f: File | null) => void;
    caps: LabCapabilities | null;
    busy: string | null;
    onUpload: () => void;
}) {
    const input = useRef<HTMLInputElement>(null);
    const tooBig = file !== null && caps !== null && file.size > caps.limits.max_file_bytes;

    return (
        <section>
            <SubLabel>File</SubLabel>
            <input
                ref={input}
                type="file"
                className="hidden"
                onChange={e => setFile(e.target.files?.[0] ?? null)}
            />
            {file ? (
                <div className="border border-white/10 rounded p-2.5 flex items-start gap-2">
                    <div className="flex-1 min-w-0">
                        <div className="text-white text-[13px] truncate" title={file.name}>
                            {file.name}
                        </div>
                        <div className="text-[11px] text-white/40">
                            {fmtBytes(file.size)} · {guessType(file.name)}
                        </div>
                    </div>
                    <button
                        onClick={() => {
                            setFile(null);
                            if (input.current) input.current.value = '';
                        }}
                        className="text-white/60 hover:text-red-400"
                        aria-label="Remove file"
                    >
                        <X size={14} />
                    </button>
                </div>
            ) : (
                <button
                    onClick={() => input.current?.click()}
                    className="w-full border border-dashed border-white/20 rounded p-4 text-[13px] text-white/50 hover:border-white/40 hover:text-white/70 transition-colors flex items-center justify-center gap-2"
                >
                    <Upload size={14} /> Choose a local file
                </button>
            )}

            {tooBig && caps && (
                <p className="text-[11.5px] text-red-300/90 mt-2">
                    This file is {fmtBytes(file.size)}; the limit is{' '}
                    {fmtBytes(caps.limits.max_file_bytes)}. Choose a smaller one — it was not
                    uploaded.
                </p>
            )}

            {caps && (
                <p className="text-[11px] text-white/60 mt-2 leading-relaxed">
                    Up to {fmtBytes(caps.limits.max_file_bytes)}. The real bytes are read in
                    your browser and sent to this machine's backend, which chunks,
                    schedules and hashes them. Nothing is written to disk.
                </p>
            )}

            {file && (
                <button
                    onClick={onUpload}
                    disabled={busy !== null || tooBig}
                    className={`${primaryBtn} mt-2 w-full`}
                >
                    {busy === 'upload' ? 'Uploading…' : 'Upload to backend'}
                </button>
            )}
        </section>
    );
}

interface SetupPanelProps {
    file: File | null;
    setFile: (f: File | null) => void;
    paths: PathInput[];
    setPaths: (p: PathInput[]) => void;
    chunkSize: number;
    setChunkSize: (n: number) => void;
    variant: string;
    setVariant: (s: string) => void;
    policy: string;
    setPolicy: (s: string) => void;
    maxRetries: number;
    setMaxRetries: (n: number) => void;
    caps: LabCapabilities | null;
    transfer: TransferView | null;
    running: boolean;
    busy: string | null;
    onUpload: () => void;
    onStart: () => void;
    onCancel: () => void;
    onLoadExample: () => void;
}

function SetupPanel({
    file, setFile, paths, setPaths, chunkSize, setChunkSize,
    variant, setVariant, policy, setPolicy, maxRetries, setMaxRetries,
    caps, transfer, running, busy, onUpload, onStart, onCancel, onLoadExample,
}: SetupPanelProps) {
    const totalBytes = paths.reduce((a, p) => a + p.tp_estimate_bps, 0);

    const setPath = (i: number, patch: Partial<PathInput>) => {
        setPaths(paths.map((p, idx) => (idx === i ? { ...p, ...patch } : p)));
    };

    return (
        <section className="border border-white/10 rounded-lg bg-black/40 flex flex-col">
            <PanelBar title="Transfer setup" />
            <div className="p-4 space-y-5">
                <FilePicker
                    file={file}
                    setFile={setFile}
                    caps={caps}
                    busy={busy}
                    onUpload={onUpload}
                />

                {/* Algorithms */}
                <section>
                    <SubLabel>Algorithms</SubLabel>
                    <Field label="Chunk scheduler">
                        <select value={variant} onChange={e => setVariant(e.target.value)} className={inputClass}>
                            {(caps?.scheduler_variants ?? []).map(o => (
                                <option key={o.id} value={o.id}>{o.label}</option>
                            ))}
                        </select>
                    </Field>
                    <Field label="Retransmission policy">
                        <select value={policy} onChange={e => setPolicy(e.target.value)} className={inputClass}>
                            {(caps?.retransmit_policies ?? []).map(o => (
                                <option key={o.id} value={o.id}>{o.label}</option>
                            ))}
                        </select>
                    </Field>
                    <p className="text-[11px] text-white/60 mt-1 leading-relaxed">
                        DB-DSP picks the path with the lowest estimated arrival time;
                        RTX-CL picks a retransmission path by congestion window when
                        delays are similar, otherwise by delay and loss together.
                    </p>
                </section>

                {/* Chunking + paths */}
                <section>
                    <SubLabel>Chunking</SubLabel>
                    <Field label="Chunk size">
                        <select value={chunkSize} onChange={e => setChunkSize(Number(e.target.value))}
                            className={inputClass}>
                            {CHUNK_PRESETS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                        </select>
                    </Field>
                    <p className="text-[11px] text-white/60 mt-1 leading-relaxed">
                        The amount of file data sent as one unit. Smaller chunks create
                        more scheduling overhead; larger chunks change how congestion
                        and retransmission bite.
                    </p>
                    {file && (
                        <p className="text-[11px] text-white/45 mt-1">
                            ≈ {Math.ceil(file.size / chunkSize).toLocaleString()} chunks for this file.
                        </p>
                    )}
                    <Field label="Max retries per chunk">
                        <input type="number" min={0} max={20} value={maxRetries}
                            onChange={e => setMaxRetries(Number(e.target.value))} className={inputClass} />
                    </Field>
                </section>

                {/* Paths */}
                <section>
                    <SubLabel>Logical paths ({paths.length})</SubLabel>
                    <div className="space-y-2">
                        {paths.map((p, i) => (
                            <div key={p.path_id} className="border border-white/10 rounded p-2 space-y-1.5">
                                <div className="flex items-center gap-2">
                                    <span className="text-[12px] text-white/70 flex-1">{p.path_id}</span>
                                    <button onClick={() => setPaths(paths.filter((_, idx) => idx !== i))}
                                        disabled={paths.length <= 1}
                                        className="text-white/60 hover:text-red-400 disabled:opacity-20"
                                        aria-label={`Remove ${p.path_id}`}>
                                        <X size={12} />
                                    </button>
                                </div>
                                <div className="grid grid-cols-3 gap-1.5">
                                    <Field label="Delay ms">
                                        <input type="number" min={0} value={p.fd_estimate_ms}
                                            onChange={e => setPath(i, { fd_estimate_ms: Number(e.target.value) })}
                                            className={inputClass} />
                                    </Field>
                                    <Field label="Mbps">
                                        <input type="number" min={1} value={Math.round(p.tp_estimate_bps / 1e6)}
                                            onChange={e => setPath(i, { tp_estimate_bps: Number(e.target.value) * 1e6 })}
                                            className={inputClass} />
                                    </Field>
                                    <Field label="Loss">
                                        <input type="number" min={0} max={1} step={0.01} value={p.loss_rate}
                                            onChange={e => setPath(i, { loss_rate: Number(e.target.value) })}
                                            className={inputClass} />
                                    </Field>
                                </div>
                            </div>
                        ))}
                    </div>
                    {paths.length < 8 && (
                        <button onClick={() => setPaths([...paths, {
                            path_id: `path-${paths.length + 1}`, fd_estimate_ms: 60,
                            tp_estimate_bps: 2_000_000, loss_rate: 0,
                        }])} className={`${ghostBtn} mt-2 w-full`}>
                            + Add path
                        </button>
                    )}
                    <p className="text-[11px] text-white/60 mt-2 leading-relaxed">
                        Paths are in-app models of delay and loss, not separate network
                        interfaces. Aggregate modelled capacity: {fmtBps(totalBytes)}.
                    </p>
                </section>

                <button onClick={onLoadExample} disabled={busy !== null} className={`${ghostBtn} w-full`}>
                    Load Example Experiment
                </button>

                {/* Run */}
                <section className="border-t border-white/10 pt-4">
                    <button onClick={onStart} disabled={busy !== null || !transfer || running}
                        className={`${primaryBtn} w-full`}>
                        <Play size={12} className="inline mr-1" />
                        {running ? 'Transferring…' : 'Start Transfer'}
                    </button>
                    {running && (
                        <button onClick={onCancel} disabled={busy !== null} className={`${ghostBtn} mt-2 w-full`}>
                            Cancel
                        </button>
                    )}
                </section>
            </div>
        </section>
    );
}

// ---------------------------------------------------------------------------
// Performance panel
// ---------------------------------------------------------------------------

function PerformancePanel({ state, onDownload, downloadAvailable, busy }: {
    state: LabState | null;
    onDownload: () => void;
    downloadAvailable: boolean;
    busy: string | null;
}) {
    const t = state?.transfer ?? null;
    const result = t?.result ?? null;
    const verified = result?.integrity_ok === true;

    return (
        <section className="border border-white/10 rounded-lg bg-black/40 flex flex-col">
            <PanelBar title="Performance"
                right={verified ? 'VERIFIED' : t?.state === 'FAILED' ? 'NOT VERIFIED' : '—'}
                rightTone={verified ? '#2dd4bf' : t?.state === 'FAILED' ? '#ef4444' : 'rgba(255,255,255,0.3)'} />
            <div className="p-4 space-y-3 text-[12px]">
                {!t && (
                    <p className="text-white/60 leading-relaxed">
                        Upload a file and start a transfer to see progress, measured
                        throughput and the SHA-256 verification result.
                    </p>
                )}
                {t && (
                    <>
                        {/* Progress */}
                        <div>
                            <div className="flex items-center justify-between text-[11px] text-white/50 mb-1">
                                <span>{t.counts.delivered} / {t.total_chunks} chunks</span>
                                <span>{t.progress_pct}%</span>
                            </div>
                            <div className="h-1.5 bg-white/10 rounded overflow-hidden">
                                <div className="h-full bg-white transition-[width]" style={{ width: `${t.progress_pct}%` }} />
                            </div>
                        </div>

                        <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
                            <Row k="File" v={t.filename} />
                            <Row k="Size" v={fmtBytes(t.file_size_bytes)} />
                            <Row k="Delivered" v={`${fmtBytes(t.counts.delivered_bytes)}`} />
                            <Row k="Elapsed" v={`${t.elapsed_s.toFixed(2)}s`} />
                            <Row k="Goodput" v={fmtBps(t.goodput_bps)} />
                            <Row k="Retransmits" v={String(t.retransmission_count)} tone={t.retransmission_count > 0 ? 'amber' : undefined} />
                            <Row k="Out of order" v={String(t.out_of_order_count)} />
                            <Row k="Chunk size" v={`${fmtBytes(t.chunk_size_bytes)}`} />
                        </dl>

                        {/* Chunk states */}
                        <div className="border border-white/10 rounded p-2.5">
                            <SubLabel>Chunk states</SubLabel>
                            <div className="grid grid-cols-5 gap-1 text-center">
                                <Stat label="pend" value={t.counts.pending} />
                                <Stat label="flight" value={t.counts.in_flight} />
                                <Stat label="retry" value={t.counts.retrying} tone="amber" />
                                <Stat label="done" value={t.counts.delivered} tone="teal" />
                                <Stat label="fail" value={t.counts.failed} tone={t.counts.failed > 0 ? 'red' : undefined} />
                            </div>
                        </div>

                        {/* Integrity */}
                        {result && (
                            <div className={`border rounded p-2.5 ${verified ? 'border-teal-500/30 bg-teal-950/10' : 'border-red-500/30 bg-red-950/10'}`}>
                                <div className="flex items-center gap-2 mb-1.5">
                                    {verified
                                        ? <CheckCircle2 size={14} className="text-teal-300" />
                                        : <AlertTriangle size={14} className="text-red-300" />}
                                    <span className={verified ? 'text-teal-200' : 'text-red-200'}>
                                        {verified ? 'Integrity verified' : 'Integrity verification failed'}
                                    </span>
                                </div>
                                <dl className="space-y-1">
                                    <Row k="Source SHA-256" v={<code className="text-[10px] break-all">{result.source_sha256}</code>} />
                                    <Row k="Received SHA-256" v={<code className="text-[10px] break-all">{result.receiver_sha256 ?? '—'}</code>} />
                                </dl>
                            </div>
                        )}

                        {/* Download */}
                        <button onClick={onDownload} disabled={!downloadAvailable || busy !== null}
                            className={`${ghostBtn} w-full flex items-center justify-center gap-2`}>
                            <Download size={13} /> Download Reconstructed File
                        </button>
                    </>
                )}
            </div>
        </section>
    );
}

// ---------------------------------------------------------------------------
// Bottom panels
// ---------------------------------------------------------------------------

function EventTimeline({ events, onClear, onExport }: {
    events: LabEvent[]; onClear: () => void; onExport: (fmt: 'json' | 'csv') => Promise<void>;
}) {
    const [fmt, setFmt] = useState<'json' | 'csv'>('json');
    const TONE: Record<string, string> = {
        info: 'text-white/55', warning: 'text-amber-300/90', error: 'text-red-400/90',
    };
    return (
        <section className="border border-white/10 rounded-lg bg-black/40 flex flex-col">
            <PanelBar title="Experiment timeline" right={`${events.length} events`} rightTone="rgba(255,255,255,0.3)" />
            <div className="px-4 pt-3 flex gap-2">
                <button onClick={onClear} className={`${ghostBtn} px-3`}>Clear Events</button>
                <select value={fmt} onChange={e => setFmt(e.target.value as 'json' | 'csv')} className="bg-transparent border border-white/15 rounded px-2 text-[12px] text-white/60">
                    <option value="json">JSON</option>
                    <option value="csv">CSV</option>
                </select>
                <button onClick={() => onExport(fmt)} className={`${ghostBtn} px-3`}>
                    Export Experiment Results
                </button>
            </div>
            <ol className="mt-3 px-4 pb-4 max-h-72 overflow-y-auto font-mono text-[11.5px] space-y-1">
                {[...events].reverse().map(e => (
                    <li key={e.seq} className="flex gap-2 border-b border-white/5 pb-1">
                        <span className="text-white/60 shrink-0 tabular-nums">
                            {new Date(e.ts * 1000).toLocaleTimeString()}
                        </span>
                        <span className="text-white/60 shrink-0 w-24 uppercase truncate">{e.type}</span>
                        <span className={TONE[e.severity] ?? 'text-white/55'}>{e.message}</span>
                    </li>
                ))}
                {events.length === 0 && <li className="text-white/60">Nothing yet. Upload a file and start a transfer.</li>}
            </ol>
        </section>
    );
}

function SchedulerPanel({ result, setResult, onError }: {
    result: SchedulerComparison | null; setResult: (r: SchedulerComparison | null) => void;
    onError: (e: string) => void;
}) {
    const [rate, setRate] = useState(5);
    const [bulk, setBulk] = useState(400);
    const [running, setRunning] = useState(false);

    const compare = async () => {
        setRunning(true);
        try {
            setResult(await xferApi.compareSchedulers({
                workload: {
                    critical_rate_hz: 40, critical_size_bits: 1600,
                    bulk_rate_hz: bulk, bulk_size_bits: 40000,
                    duration_ms: 500, max_queue: 200, seed: 4242,
                },
                link: { rate_bps: rate * 1e6, propagation_ms: 1, max_queue: 200 },
            }));
        } catch (e) {
            onError(message(e));
        } finally {
            setRunning(false);
        }
    };

    return (
        <section className="border border-white/10 rounded-lg bg-black/40 flex flex-col">
            <PanelBar title="FIFO vs Priority"
                right={result ? (result.simulated ? 'SIMULATED' : 'MEASURED') : '—'}
                rightTone="#f5b54a" />
            <div className="p-4 space-y-3 text-[12px]">
                <p className="text-[11px] text-white/40 leading-relaxed">
                    Runs the same seeded workload through both schedulers over one
                    constrained link. Results are queueing-model output, labelled
                    below as simulated — not a measurement of real sockets.
                </p>
                <div className="grid grid-cols-2 gap-2">
                    <Field label="Link Mbps">
                        <input type="number" min={1} value={rate} onChange={e => setRate(Number(e.target.value))} className={inputClass} />
                    </Field>
                    <Field label="Bulk pkt/s">
                        <input type="number" min={1} value={bulk} onChange={e => setBulk(Number(e.target.value))} className={inputClass} />
                    </Field>
                </div>
                <button onClick={compare} disabled={running} className={`${primaryBtn} w-full`}>
                    <RefreshCw size={12} className={`inline mr-1 ${running ? 'animate-spin' : ''}`} />
                    {running ? 'Running…' : 'Run FIFO vs Priority'}
                </button>

                {result && (
                    <div className="space-y-2">
                        <div className="text-[11px] text-white/40">
                            {result.congested ? 'Link is congested (offered load exceeds capacity).' : 'Offered load is below capacity.'}
                        </div>
                        <table className="w-full text-[11px]">
                            <thead>
                                <tr className="text-white/40 text-left">
                                    <th className="font-normal pb-1">Scheduler</th>
                                    <th className="font-normal pb-1">Crit lat (ms)</th>
                                    <th className="font-normal pb-1">Bulk tput</th>
                                    <th className="font-normal pb-1">Drops</th>
                                </tr>
                            </thead>
                            <tbody>
                                {result.results.map(r => (
                                    <tr key={r.scheduler}>
                                        <td className="text-white/70">{r.scheduler}</td>
                                        <td className="text-white/60">{fmtNum(r.classes.critical?.latency_median_ms)}</td>
                                        <td className="text-white/60">{fmtBps(r.classes.bulk?.throughput_bps ?? 0)}</td>
                                        <td className="text-white/60">{(r.classes.critical?.packets_dropped ?? 0) + (r.classes.bulk?.packets_dropped ?? 0)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        <div className="border border-white/10 rounded p-2">
                            <SubLabel>Findings</SubLabel>
                            <ul className="space-y-1">
                                {result.findings.slice(0, 4).map((f, i) => (
                                    <li key={i} className="text-[11px] text-white/50 leading-relaxed">• {f}</li>
                                ))}
                            </ul>
                        </div>
                        <p className="text-[10.5px] text-amber-300/70 leading-relaxed">
                            {result.caveat}
                        </p>
                    </div>
                )}
            </div>
        </section>
    );
}

function VariantPanel({ result, setResult, paths, onError }: {
    result: VariantComparison | null; setResult: (r: VariantComparison | null) => void;
    paths: PathInput[]; onError: (e: string) => void;
}) {
    const [running, setRunning] = useState(false);
    const [reps, setReps] = useState(3);

    const compare = async () => {
        setRunning(true);
        try {
            setResult(await xferApi.compareVariants({
                paths, payload_bytes: 120_000, chunk_size_bytes: 4096,
                repetitions: reps,
                variants: ['DB-DSP', 'RR', 'WRR', 'SINGLE', 'NAIVE_DELAY'],
                retransmit_policies: ['RTX-CL', 'RTX-CWND'], seed: 1337,
            }));
        } catch (e) {
            onError(message(e));
        } finally {
            setRunning(false);
        }
    };

    return (
        <section className="border border-white/10 rounded-lg bg-black/40 flex flex-col">
            <PanelBar title="Research algorithm comparison"
                right={result ? (result.simulated ? 'SIMULATED' : 'MEASURED') : '—'}
                rightTone="#f5b54a" />
            <div className="p-4 space-y-3 text-[12px]">
                <p className="text-[11px] text-white/40 leading-relaxed">
                    DB-DSP and its four baselines (RR, WRR, Single-Path, Naïve
                    Delay-Aware) across RTX-CL and RTX-CWND, over identical payloads.
                    Delay and loss are the transfer session's modelled profiles.
                </p>
                <Field label="Repetitions">
                    <input type="number" min={1} max={10} value={reps} onChange={e => setReps(Number(e.target.value))} className={inputClass} />
                </Field>
                <button onClick={compare} disabled={running} className={`${primaryBtn} w-full`}>
                    <RefreshCw size={12} className={`inline mr-1 ${running ? 'animate-spin' : ''}`} />
                    {running ? 'Running…' : 'Compare All Variants'}
                </button>

                {result && (
                    <div className="overflow-x-auto">
                        <table className="w-full text-[11px]">
                            <thead>
                                <tr className="text-white/40 text-left">
                                    <th className="font-normal pb-1">Variant</th>
                                    <th className="font-normal pb-1">RTX</th>
                                    <th className="font-normal pb-1">Dur (s)</th>
                                    <th className="font-normal pb-1">OOO</th>
                                    <th className="font-normal pb-1">SHA</th>
                                </tr>
                            </thead>
                            <tbody>
                                {result.rows.map(r => (
                                    <tr key={`${r.variant}|${r.retransmit_policy}`}>
                                        <td className="text-white/70">{r.variant}</td>
                                        <td className="text-white/50">{r.retransmit_policy}</td>
                                        <td className="text-white/60">{fmtNum(r.duration_s_median)}</td>
                                        <td className="text-white/60">{fmtNum(r.out_of_order_median)}</td>
                                        <td className="text-white/60">{r.integrity_ok}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        <p className="text-[10.5px] text-amber-300/70 leading-relaxed mt-2">
                            {result.caveat}
                        </p>
                    </div>
                )}
            </div>
        </section>
    );
}

// ---------------------------------------------------------------------------
// Bits
// ---------------------------------------------------------------------------

function DeliveryModelBar({ state }: { state: LabState | null }) {
    return (
        <div className="border-t border-white/10 px-4 py-2.5 text-[11.5px] text-white/40 leading-relaxed">
            {state?.delivery_model ?? 'Upload a file to begin.'}
        </div>
    );
}

function PanelBar({ title, right, rightTone }: { title: string; right?: string; rightTone?: string }) {
    return (
        <div className="flex items-center justify-between px-4 h-11 border-b border-white/10 shrink-0">
            <h2 className="text-[12px] uppercase tracking-[0.14em] text-white/60">{title}</h2>
            {right && <span className="text-[11px] font-mono" style={{ color: rightTone ?? 'rgba(255,255,255,0.4)' }}>{right}</span>}
        </div>
    );
}

function SubLabel({ children }: { children: React.ReactNode }) {
    return <h3 className="text-[10.5px] uppercase tracking-[0.14em] text-white/60 mb-1.5">{children}</h3>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <label className="block">
            <span className="block text-[10.5px] text-white/60 mb-1">{label}</span>
            {children}
        </label>
    );
}

function Row({ k, v, tone }: { k: string; v: React.ReactNode; tone?: 'amber' | 'red' | 'teal' }) {
    const colour = tone === 'amber' ? 'text-amber-300/90' : tone === 'red' ? 'text-red-400/90' : tone === 'teal' ? 'text-teal-300/90' : 'text-white/70';
    return (
        <div className="flex gap-2">
            <dt className="text-white/60 w-24 shrink-0">{k}</dt>
            <dd className={`min-w-0 break-words ${colour}`}>{v}</dd>
        </div>
    );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: 'teal' | 'red' | 'amber' }) {
    const colour = tone === 'teal' ? 'text-teal-300' : tone === 'red' ? 'text-red-400' : tone === 'amber' ? 'text-amber-300' : 'text-white/70';
    return (
        <div>
            <div className={`text-[14px] tabular-nums ${colour}`}>{value}</div>
            <div className="text-[10px] text-white/60">{label}</div>
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

function fmtBytes(n: number) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`;
    return `${(n / (1024 * 1024)).toFixed(2)} MiB`;
}

function fmtBps(bps: number) {
    if (!bps) return '—';
    if (bps >= 1e6) return `${(bps / 1e6).toFixed(2)} Mbps`;
    if (bps >= 1e3) return `${(bps / 1e3).toFixed(1)} kbps`;
    return `${Math.round(bps)} bps`;
}

function fmtNum(n: number | null | undefined) {
    return n === null || n === undefined ? '—' : n.toFixed(3);
}

function guessType(name: string) {
    const ext = name.split('.').pop()?.toUpperCase() ?? '';
    return ext ? ext.toLowerCase() : 'file';
}

function message(exc: unknown) {
    if (exc instanceof ApiError) return exc.message;
    if (exc instanceof Error) return exc.message;
    return 'Something went wrong.';
}