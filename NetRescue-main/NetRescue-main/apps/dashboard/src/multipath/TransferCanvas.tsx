/**
 * TransferCanvas.tsx — the live network view.
 *
 * Three fixed endpoints — source on the left, destination on the right — joined
 * by one line per logical path. Chunks travel along a line only while the
 * backend says a chunk is in flight on that path, so the picture is a reading of
 * transfer state rather than a decoration running beside it.
 *
 * Two things this deliberately does not do:
 *
 * It does not draw separate network interfaces. The paths are modelled
 * delay-and-loss profiles inside one process (see the backend's `_deliver`), and
 * the canvas says "logical path" for each one rather than implying a cable.
 *
 * It does not animate a chunk that has no in-flight record. A chunk appears when
 * a `chunk.assigned` event carries its index and a path, and is removed when the
 * same chunk is reported delivered, lost or failed. With nothing in flight the
 * canvas is empty, which is the honest picture of an idle link.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import type { ChunkView, LabPath } from './types';

const VIEW_W = 960;
const VIEW_H = 420;

const SOURCE = { x: 90, y: VIEW_H / 2 };
const DEST = { x: VIEW_W - 90, y: VIEW_H / 2 };

interface FlyingChunk {
    chunkIndex: number;
    pathId: string;
    /** Fraction along the path, 0..1. */
    progress: number;
    attempts: number;
}

interface Props {
    paths: LabPath[];
    chunks: ChunkView[];
    running: boolean;
    transferId: string | null;
}

function pathEnd(index: number, total: number) {
    if (total <= 1) return VIEW_H / 2;
    const spread = Math.min(230, 40 + total * 26);
    const top = VIEW_H / 2 - spread / 2;
    const step = total <= 1 ? 0 : spread / (total - 1);
    return top + index * step;
}

export default function TransferCanvas({ paths, chunks, running, transferId }: Props) {
    const [flying, setFlying] = useState<FlyingChunk[]>([]);
    const [, force] = useState(0);
    const frame = useRef<number | null>(null);
    const position = useRef<Map<string, number>>(new Map());
    const lastKey = useRef<string>('');

    const byId = useMemo(() => new Map(chunks.map(c => [c.chunk_index, c])), [chunks]);

    /**
     * Chunks the backend currently reports as in flight, keyed by chunk index.
     *
     * Read from chunk state rather than from the event stream so a chunk that was
     * assigned and then delivered between two polls is never shown as still
     * travelling.
     */
    const inFlight = useMemo(
        () =>
            chunks
                .filter(c => c.status === 'IN_FLIGHT' && c.path_id)
                .map(c => ({ index: c.chunk_index, pathId: c.path_id as string,
                             attempts: c.attempts })),
        [chunks],
    );

    const inFlightKey = inFlight
        .map(c => `${c.index}:${c.pathId}`)
        .sort()
        .join(',');

    // Restart the flight positions whenever the transfer identity changes, so a
    // second run does not inherit the first run's mid-path positions.
    useEffect(() => {
        if (transferId !== lastKey.current) {
            lastKey.current = transferId ?? '';
            position.current.clear();
            setFlying([]);
        }
    }, [transferId]);

    useEffect(() => {
        if (!running) {
            setFlying([]);
            return;
        }
        let cancelled = false;

        const tick = () => {
            if (cancelled) return;
            const next: FlyingChunk[] = [];
            const live = new Set(inFlight.map(c => `${c.index}:${c.pathId}`));

            for (const chunk of inFlight) {
                const key = `${chunk.index}:${chunk.pathId}`;
                // Drop positions for chunks that are no longer in flight.
                if (!live.has(key)) continue;
                // Advance at a fixed visual rate. This is motion only — the chunk
                // exists because the backend put it on this path.
                const current = (position.current.get(key) ?? 0) + 0.035;
                position.current.set(key, current);
                next.push({
                    chunkIndex: chunk.index,
                    pathId: chunk.pathId,
                    progress: current,
                    attempts: chunk.attempts,
                });
            }

            // Retire chunks that reached the far end or left the in-flight set.
            for (const key of Array.from(position.current.keys())) {
                if (!live.has(key) || position.current.get(key)! > 1.35) {
                    position.current.delete(key);
                }
            }

            setFlying(next);
            frame.current = requestAnimationFrame(tick);
        };

        frame.current = requestAnimationFrame(tick);
        return () => {
            cancelled = true;
            if (frame.current !== null) cancelAnimationFrame(frame.current);
        };
    }, [running, inFlightKey]); // eslint-disable-line react-hooks/exhaustive-deps

    // A completed transfer redraws once so the final delivered counts are on screen.
    useEffect(() => {
        if (!running) force(n => n + 1);
    }, [running, transferId]);

    const delivered = chunks.filter(c => c.status === 'DELIVERED');
    const inFlightSet = new Set(inFlight.map(c => c.index));
    const retrying = chunks.filter(c => c.status === 'RETRYING');

    return (
        <div className="relative w-full h-full">
            <svg
                viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
                preserveAspectRatio="xMidYMid meet"
                className="w-full h-full select-none"
                style={{ background: 'radial-gradient(ellipse at 25% 0%, #1a0508 0%, #000 62%)' }}
            >
                <defs>
                    <pattern id="xfer-grid" width="40" height="40" patternUnits="userSpaceOnUse">
                        <path d="M 40 0 L 0 0 0 40" fill="none"
                            stroke="rgba(255,255,255,0.035)" strokeWidth="1" />
                    </pattern>
                    <filter id="xfer-glow" x="-50%" y="-50%" width="200%" height="200%">
                        <feGaussianBlur stdDeviation="3" result="b" />
                        <feMerge>
                            <feMergeNode in="b" />
                            <feMergeNode in="SourceGraphic" />
                        </feMerge>
                    </filter>
                </defs>

                <rect width={VIEW_W} height={VIEW_H} fill="url(#xfer-grid)" />

                {/* Paths */}
                {paths.map((path, i) => {
                    const y = pathEnd(i, paths.length);
                    const busy = flying.some(f => f.pathId === path.path_id);
                    const down = path.status !== 'UP';
                    const colour = down ? '#ef4444' : busy ? '#ffffff' : 'rgba(255,255,255,0.22)';
                    return (
                        <g key={path.path_id}>
                            <line
                                x1={SOURCE.x} y1={y} x2={DEST.x} y2={y}
                                stroke={colour}
                                strokeWidth={busy ? 2.5 : 1.5}
                                strokeDasharray={down ? '7 5' : undefined}
                                opacity={busy ? 0.95 : 1}
                            />
                            {/* Midpoint label */}
                            <text
                                x={VIEW_W / 2}
                                y={y - 9}
                                textAnchor="middle"
                                fill={down ? '#ef4444' : busy ? '#ffffff' : 'rgba(255,255,255,0.45)'}
                                fontSize="10"
                                style={{ fontFamily: '"Space Mono", monospace' }}
                            >
                                {path.path_id} · {path.fd_estimate_ms}ms
                            </text>
                            <text
                                x={VIEW_W / 2}
                                y={y + 16}
                                textAnchor="middle"
                                fill="rgba(255,255,255,0.3)"
                                fontSize="9"
                                style={{ fontFamily: '"Space Mono", monospace' }}
                            >
                                logical · {path.chunks_sent} sent
                                {path.retransmissions > 0 ? ` · ${path.retransmissions} retx` : ''}
                            </text>
                        </g>
                    );
                })}

                {/* Chunks actually in flight on a path */}
                {flying.map(f => {
                    const i = paths.findIndex(p => p.path_id === f.pathId);
                    if (i < 0) return null;
                    const y = pathEnd(i, paths.length);
                    const x = SOURCE.x + (DEST.x - SOURCE.x) * Math.min(1, f.progress);
                    return (
                        <g key={`${f.chunkIndex}:${f.pathId}`} pointerEvents="none">
                            <circle cx={x} cy={y} r={8} fill="rgba(255,255,255,0.12)" />
                            <circle cx={x} cy={y} r={3.5} fill="#ffffff" filter="url(#xfer-glow)" />
                        </g>
                    );
                })}

                {/* Endpoints */}
                <circle cx={SOURCE.x} cy={SOURCE.y} r={28}
                    fill="rgba(0,0,0,0.85)" stroke="#2dd4bf" strokeWidth="1.75" />
                <text x={SOURCE.x} y={SOURCE.y - 1} textAnchor="middle" fill="#fff"
                    fontSize="12" style={{ fontFamily: '"Space Mono", monospace' }}>SRC</text>
                <text x={SOURCE.x} y={SOURCE.y + 46} textAnchor="middle" fill="rgba(255,255,255,0.5)"
                    fontSize="9" style={{ fontFamily: '"Space Mono", monospace' }}>this browser</text>

                <circle cx={DEST.x} cy={DEST.y} r={28}
                    fill="rgba(0,0,0,0.85)"
                    stroke={delivered.length > 0 ? '#2dd4bf' : 'rgba(255,255,255,0.3)'}
                    strokeWidth="1.75" />
                <text x={DEST.x} y={DEST.y - 1} textAnchor="middle" fill="#fff"
                    fontSize="12" style={{ fontFamily: '"Space Mono", monospace' }}>DST</text>
                <text x={DEST.x} y={DEST.y + 46} textAnchor="middle" fill="rgba(255,255,255,0.5)"
                    fontSize="9" style={{ fontFamily: '"Space Mono", monospace' }}>
                    reassembled by offset
                </text>

                {paths.length === 0 && (
                    <text x={VIEW_W / 2} y={VIEW_H / 2} textAnchor="middle"
                        fill="rgba(255,255,255,0.3)" fontSize="13"
                        style={{ fontFamily: '"Space Mono", monospace' }}>
                        Configure at least one path to begin.
                    </text>
                )}
            </svg>

            {/* Legend and live counts */}
            <div className="absolute bottom-2 left-3 right-3 flex flex-wrap items-center gap-x-4 gap-y-1
                            text-[11px] text-white/60 pointer-events-none">
                <span className="flex items-center gap-1.5">
                    <span className="inline-block w-4" style={{ borderTop: '2px solid #2dd4bf', height: 0 }} />
                    source / destination
                </span>
                <span className="flex items-center gap-1.5">
                    <span className="inline-block w-4" style={{ borderTop: '2px solid #ffffff', height: 0 }} />
                    carrying chunks now
                </span>
                <span className="flex items-center gap-1.5">
                    <span className="inline-block w-4" style={{ borderTop: '2px dashed #ef4444', height: 0 }} />
                    path down
                </span>
                <span className="ml-auto">
                    in flight {inFlightSet.size} · retrying {retrying.length} · delivered {delivered.length}
                </span>
            </div>
        </div>
    );
}