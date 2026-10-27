/**
 * Pure helpers behind the Dijkstra view. Kept separate from the page so the
 * stepping, formatting and staleness rules can be unit-tested without a DOM.
 */
import type { GGraph, Iteration, RoutingRow } from './api';

/** Infinity (no path known yet) is `null` in API data and is never shown as 0. */
export const INF = '∞';

export function fmtDistance(d: number | null | undefined): string {
    if (d === null || d === undefined) return INF;
    return String(Number(d.toFixed(6)));
}

export function fmtPred(p: string | null | undefined): string {
    return p ? p : '—';
}

/** The inputs that determine a Dijkstra result. Equal keys ⇒ result is current. */
export function requestKey(graph: GGraph, source: string, target: string): string {
    const links = graph.links
        .map(l => ({ a: l.a, b: l.b, cost: l.cost, up: l.up !== false }))
        .sort((x, y) => (x.a + x.b).localeCompare(y.a + y.b));
    return JSON.stringify({ nodes: [...graph.nodes].sort(), links, source, target });
}

export type StepAction = 'first' | 'prev' | 'next' | 'last' | { to: number };

export function stepReducer(step: number, action: StepAction, total: number): number {
    const last = Math.max(0, total - 1);
    if (action === 'first') return 0;
    if (action === 'last') return last;
    if (action === 'prev') return Math.max(0, step - 1);
    if (action === 'next') return Math.min(last, step + 1);
    return Math.min(last, Math.max(0, action.to));
}

/** Which distance / predecessor cells changed between two consecutive snapshots. */
export function changedCells(prev: Iteration | undefined, cur: Iteration): { dist: Set<string>; pred: Set<string> } {
    const dist = new Set<string>();
    const pred = new Set<string>();
    if (!prev) return { dist, pred };
    for (const n of Object.keys(cur.distances)) {
        if (cur.distances[n] !== prev.distances[n]) dist.add(n);
        if (cur.predecessors[n] !== prev.predecessors[n]) pred.add(n);
    }
    return { dist, pred };
}

/** Links on the shortest-path tree implied by a snapshot's predecessors. */
export function treeLinks(it: Iteration): Set<string> {
    const ids = new Set<string>();
    for (const [n, p] of Object.entries(it.predecessors)) {
        if (p) ids.add([n, p].sort().join('-'));
    }
    return ids;
}

/** Links relaxed (and improved) in a snapshot. */
export function relaxedLinks(it: Iteration): Set<string> {
    return new Set(it.relaxed.filter(r => r.improved).map(r => r.via_link));
}

export function describeRow(row: RoutingRow, source: string): { next: string; cost: string; path: string; status: string } {
    if (row.status === 'source') {
        return { next: `— (local, ${source})`, cost: '0', path: row.path.join(' → '), status: 'Source' };
    }
    if (row.status === 'unreachable') {
        return { next: '—', cost: INF, path: '—', status: 'Unreachable' };
    }
    return { next: row.next_hop ?? '—', cost: fmtDistance(row.cost), path: row.path.join(' → '), status: 'Reachable' };
}
