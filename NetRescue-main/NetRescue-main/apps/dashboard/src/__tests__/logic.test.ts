import { describe, expect, it } from 'vitest';
import { features } from '../data/features';
import { FEATURE_MODE, FEATURE_WORKFLOW, JOURNEY, WORKFLOWS } from '../labs/workflows';
import {
    changedCells, describeRow, fmtDistance, fmtPred, relaxedLinks, requestKey, stepReducer, treeLinks,
} from '../labs/dijkstraView';
import type { GGraph, SolveResponse } from '../labs/api';
import solveExample from './fixtures/solve_example.json';
import solveUnreachable from './fixtures/solve_unreachable.json';
import routingExample from './fixtures/routing_example.json';

const solve = solveExample as unknown as SolveResponse;
const unreachable = solveUnreachable as unknown as SolveResponse;
const graph = routingExample.graph as unknown as GGraph;

describe('experiment workflows', () => {
    const experiments = ['hysteresis', 'multipath', 'diagnostics', 'routing', 'tcp', 'shaping', 'tcp-udp', 'integration'];

    it('exist for every experiment in the build, each with five filled-in steps', () => {
        for (const id of [...experiments, 'suite']) {
            const wf = WORKFLOWS[id];
            expect(wf, id).toBeDefined();
            expect(wf.steps).toHaveLength(5);
            for (const s of wf.steps) {
                expect(s.title.length).toBeGreaterThan(3);
                expect(s.text.length).toBeGreaterThan(20);
            }
        }
    });

    it('are specific: no two experiments share a step text', () => {
        const seen = new Map<string, string>();
        for (const wf of Object.values(WORKFLOWS)) {
            for (const s of wf.steps) {
                expect(seen.get(s.text), `"${s.text}" reused by ${wf.id}`).toBeUndefined();
                seen.set(s.text, wf.id);
            }
        }
    });

    it('put configuration and the action before inspecting results', () => {
        for (const id of experiments) {
            const titles = WORKFLOWS[id].steps.map(s => s.title.toLowerCase()).join('|');
            expect(titles, id).toMatch(/inspect/);
        }
        expect(WORKFLOWS.routing.steps[0].title).toBe('Build your network');
        expect(WORKFLOWS.routing.steps[4].title).toBe('Introduce a change');
    });

    it('are attached to every feature on the features page', () => {
        for (const f of features) {
            expect(FEATURE_WORKFLOW[f.id], f.id).toBeDefined();
            expect(WORKFLOWS[FEATURE_WORKFLOW[f.id]]).toBeDefined();
        }
        for (const mode of Object.values(FEATURE_MODE)) expect(WORKFLOWS[mode]).toBeDefined();
    });
});

describe('navigation data', () => {
    it('lists four features, not the five lab pages as separate features', () => {
        expect(features.map(f => f.id)).toEqual(['feature-a', 'feature-c', 'feature-d', 'feature-e']);
    });

    it('keeps the five lab pages in one ordered journey', () => {
        expect(JOURNEY.map(j => j.id)).toEqual(['routing', 'tcp', 'shaping', 'tcp-udp', 'integration']);
        expect(JOURNEY.map(j => j.path)).toEqual([
            '/features/routing/playground', '/features/tcp-congestion/playground',
            '/features/traffic-shaping/playground', '/features/tcp-udp/playground',
            '/features/integration/playground',
        ]);
        for (const j of JOURNEY) expect(WORKFLOWS[j.id]).toBeDefined();
    });
});

describe('dijkstra view helpers', () => {
    it('shows infinity as ∞ and never as zero', () => {
        expect(fmtDistance(null)).toBe('∞');
        expect(fmtDistance(undefined)).toBe('∞');
        expect(fmtDistance(0)).toBe('0');
        expect(fmtDistance(2.5)).toBe('2.5');
        expect(fmtPred(null)).toBe('—');
        expect(fmtPred('A')).toBe('A');
    });

    it('steps within bounds', () => {
        expect(stepReducer(3, 'prev', 7)).toBe(2);
        expect(stepReducer(0, 'prev', 7)).toBe(0);
        expect(stepReducer(5, 'next', 7)).toBe(6);
        expect(stepReducer(6, 'next', 7)).toBe(6);
        expect(stepReducer(2, 'first', 7)).toBe(0);
        expect(stepReducer(2, 'last', 7)).toBe(6);
        expect(stepReducer(2, { to: 99 }, 7)).toBe(6);
        expect(stepReducer(2, { to: -4 }, 7)).toBe(0);
        expect(stepReducer(0, 'last', 0)).toBe(0);
    });

    it('detects which cells changed between recorded iterations', () => {
        const [, i1, i2] = solve.iterations;
        const ch = changedCells(i1, i2);       // selecting B relaxes D and E
        expect([...ch.dist].sort()).toEqual(['D', 'E']);
        expect([...ch.pred].sort()).toEqual(['D', 'E']);
        expect(changedCells(undefined, i1).dist.size).toBe(0);
    });

    it('derives tree and relaxed links from a recorded iteration', () => {
        const it2 = solve.iterations[2];
        expect([...treeLinks(it2)].sort()).toEqual(['A-B', 'A-C', 'B-D', 'B-E']);
        expect([...relaxedLinks(it2)].sort()).toEqual(['B-D', 'B-E']);
    });

    it('builds a request key that changes with weights, link state, source and target', () => {
        const base = requestKey(graph, 'A', 'F');
        const heavier = { ...graph, links: graph.links.map(l => l.id === 'B-D' ? { ...l, cost: 10 } : l) };
        const disabled = { ...graph, links: graph.links.map(l => l.id === 'B-D' ? { ...l, up: false } : l) };
        expect(requestKey(heavier, 'A', 'F')).not.toBe(base);
        expect(requestKey(disabled, 'A', 'F')).not.toBe(base);
        expect(requestKey(graph, 'B', 'F')).not.toBe(base);
        expect(requestKey(graph, 'A', 'E')).not.toBe(base);
        // order of nodes/links must not matter, an explicit up:true equals the default
        const shuffled = { nodes: [...graph.nodes].reverse(), links: [...graph.links].reverse().map(l => ({ ...l, up: true })) };
        expect(requestKey(shuffled, 'A', 'F')).toBe(base);
    });

    it('describes source, reachable and unreachable routing-table rows distinctly', () => {
        const rows = Object.fromEntries(solve.routing_table.map(r => [r.destination, r]));
        expect(describeRow(rows.A, 'A')).toMatchObject({ status: 'Source', cost: '0' });
        expect(describeRow(rows.A, 'A').next).toContain('local');
        expect(describeRow(rows.F, 'A')).toEqual({ next: 'B', cost: '6', path: 'A → B → D → F', status: 'Reachable' });
        const z = unreachable.routing_table.find(r => r.destination === 'Z')!;
        expect(describeRow(z, 'A')).toEqual({ next: '—', cost: '∞', path: '—', status: 'Unreachable' });
    });

    it('the recorded fixture really contains infinity (null) and zero', () => {
        expect(solve.iterations[0].distances.B).toBeNull();
        expect(solve.iterations[0].distances.A).toBe(0);
    });
});
