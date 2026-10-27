import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ExecutionProfile, GGraph, SolveResponse } from '../labs/api';
import exampleFixture from './fixtures/routing_example.json';
import solveFixture from './fixtures/solve_example.json';
import unreachableFixture from './fixtures/solve_unreachable.json';
import profileFixture from './fixtures/execution_profile.json';

vi.mock('../labs/api', async importOriginal => {
    const actual = await importOriginal<typeof import('../labs/api')>();
    return {
        ...actual,
        labsApi: {
            ...actual.labsApi,
            routingExample: vi.fn(), solve: vi.fn(), whatIf: vi.fn(), execution: vi.fn(),
        },
    };
});

import { ApiError, labsApi } from '../labs/api';
import { resetExecutionProfileCache } from '../labs/ui';
import RoutingLabPage from '../pages/RoutingLabPage';

const solved = solveFixture as unknown as SolveResponse;
const unreachable = unreachableFixture as unknown as SolveResponse;
const solve = vi.mocked(labsApi.solve);

function mount() {
    return render(<MemoryRouter><RoutingLabPage /></MemoryRouter>);
}

/** Wait for the first automatic run to finish. */
async function ready() {
    await screen.findByRole('table', { name: 'Dijkstra iteration table' }, { timeout: 3000 });
}

const lastGraph = (): GGraph => solve.mock.calls[solve.mock.calls.length - 1][0];
const lastCall = () => solve.mock.calls[solve.mock.calls.length - 1];

beforeEach(() => {
    resetExecutionProfileCache();
    vi.mocked(labsApi.routingExample).mockReset().mockResolvedValue(exampleFixture as never);
    vi.mocked(labsApi.execution).mockReset().mockResolvedValue(profileFixture as unknown as ExecutionProfile);
    vi.mocked(labsApi.whatIf).mockReset();
    solve.mockReset().mockResolvedValue(solved);
});

describe('Dijkstra lab — workflow and mode', () => {
    it('shows the run guide and the execution mode before the explanation', async () => {
        mount();
        await ready();
        expect(screen.queryByTestId('workflow')).toBeNull();
        const about = screen.getByRole('region', { name: 'About this experiment' });
        await waitFor(() => expect(screen.getByTestId('mode-panel')).toHaveAttribute('data-mode', 'SIMULATION'));
        expect(about).toHaveTextContent(/not a routing protocol/i);
        expect(about).toHaveTextContent(/distance-vector routing is not implemented/i);
    });

    it('labels the result as a SIMULATION using the backend execution block', async () => {
        mount();
        await ready();
        const exec = screen.getAllByTestId('execution')[0];
        expect(exec).toHaveAttribute('data-mode', 'SIMULATION');
        expect(exec).toHaveTextContent('Dijkstra');
    });
});

describe('Dijkstra lab — iteration table', () => {
    it('runs automatically and shows every recorded iteration, ending at the final one', async () => {
        mount();
        await ready();
        expect(solve).toHaveBeenCalledTimes(1);
        const table = screen.getByRole('table', { name: 'Dijkstra iteration table' });
        const rows = within(table).getAllByTestId(/^iter-row-/);
        expect(rows).toHaveLength(solved.iterations.length);        // 0..6
        expect(screen.getByTestId('step-label')).toHaveTextContent('Iteration 6 of 6');
        // header has one distance column and one predecessor column per node
        expect(within(table).getAllByRole('columnheader', { name: 'F' })).toHaveLength(2);
    });

    it('shows ∞ for unknown distances and a real 0 for the source', async () => {
        mount();
        await ready();
        const user = userEvent.setup();
        await user.click(screen.getByRole('button', { name: 'First iteration' }));
        const row0 = screen.getByTestId('iter-row-0');
        expect(within(row0).getAllByText('∞')).toHaveLength(5);
        expect(row0.querySelector('[data-cell="d-A"]')).toHaveTextContent(/^0$/);
        expect(row0.querySelector('[data-cell="d-B"]')).toHaveTextContent('∞');
        expect(row0.querySelector('[data-cell="p-B"]')).toHaveTextContent('—');
        expect(screen.getByTestId('step-note')).toHaveTextContent('Initialise');
    });

    it('steps forward and backward through the recorded iterations only', async () => {
        mount();
        await ready();
        const user = userEvent.setup();
        await user.click(screen.getByRole('button', { name: 'First iteration' }));
        expect(screen.getAllByTestId(/^iter-row-/)).toHaveLength(1);
        expect(screen.getByRole('button', { name: 'Previous iteration' })).toBeDisabled();

        await user.click(screen.getByRole('button', { name: 'Next iteration' }));
        await user.click(screen.getByRole('button', { name: 'Next iteration' }));
        expect(screen.getByTestId('step-label')).toHaveTextContent('Iteration 2 of 6');
        expect(screen.getAllByTestId(/^iter-row-/)).toHaveLength(3);
        expect(screen.getByTestId('iter-row-2')).toHaveAttribute('data-current', 'true');
        // iteration 2 selected B and relaxed D and E to 5 and 3
        const row2 = screen.getByTestId('iter-row-2');
        expect(row2.querySelector('[data-cell="d-D"]')).toHaveTextContent('5');
        expect(row2.querySelector('[data-cell="d-E"]')).toHaveTextContent('3');
        expect(row2.querySelector('[data-cell="p-D"]')).toHaveTextContent('B');
        expect(screen.getByTestId('step-note')).toHaveTextContent(/Select B/);

        await user.click(screen.getByRole('button', { name: 'Previous iteration' }));
        expect(screen.getByTestId('step-label')).toHaveTextContent('Iteration 1 of 6');
        expect(screen.queryByTestId('iter-row-2')).toBeNull();

        await user.click(screen.getByRole('button', { name: 'Final iteration' }));
        expect(screen.getByTestId('step-label')).toHaveTextContent('Iteration 6 of 6');
        expect(screen.getByRole('button', { name: 'Next iteration' })).toBeDisabled();
    });

    it('highlights the current, visited and relaxed parts of the topology for the viewed iteration', async () => {
        mount();
        await ready();
        const user = userEvent.setup();
        const svg = () => screen.getByRole('img', { name: /Topology/ });
        await user.click(screen.getByRole('button', { name: 'First iteration' }));
        await user.click(screen.getByRole('button', { name: 'Next iteration' }));
        await user.click(screen.getByRole('button', { name: 'Next iteration' }));       // B selected
        expect(svg().querySelector('[data-node="B"]')).toHaveAttribute('data-state', 'current');
        expect(svg().querySelector('[data-node="A"]')).toHaveAttribute('data-state', 'visited');
        expect(svg().querySelector('[data-node="F"]')).toHaveAttribute('data-state', 'unvisited');
        expect(svg().querySelector('[data-link="B-D"]')).toHaveAttribute('data-state', 'relaxed');
        expect(svg().querySelector('[data-link="B-E"]')).toHaveAttribute('data-state', 'relaxed');
        expect(svg().querySelector('[data-link="A-B"]')).toHaveAttribute('data-state', 'tree');
        expect(svg().querySelector('[data-link="E-F"]')).toHaveAttribute('data-state', 'idle');
        expect(svg()).toHaveTextContent('d=∞');                // F not reached yet
        await user.click(screen.getByRole('button', { name: 'Final iteration' }));
        expect(svg().querySelector('[data-link="D-F"]')).toHaveAttribute('data-state', 'path');
        expect(svg().querySelector('[data-link="B-D"]')).toHaveAttribute('data-state', 'path');
    });
});

describe('Dijkstra lab — final routing table', () => {
    it('shows one row per node with cost, next hop, complete path and status', async () => {
        mount();
        await ready();
        const table = screen.getByRole('table', { name: 'Final routing table' });
        expect(within(table).getAllByTestId(/^rt-/)).toHaveLength(6);
        expect(within(table).getAllByRole('columnheader').map(h => h.textContent)).toEqual([
            'Destination', 'Shortest distance / cost', 'Next hop', 'Complete path', 'Status']);
        const f = screen.getByTestId('rt-F');
        expect(f).toHaveTextContent('F');
        expect(f).toHaveTextContent('6');
        expect(f).toHaveTextContent('B');
        expect(f).toHaveTextContent('A → B → D → F');
        expect(f).toHaveTextContent('Reachable');
        const a = screen.getByTestId('rt-A');
        expect(a).toHaveAttribute('data-status', 'source');
        expect(a).toHaveTextContent(/local/);
        expect(a).toHaveTextContent('Source');
    });

    it('is withheld until the algorithm has finished', async () => {
        mount();
        await ready();
        const user = userEvent.setup();
        await user.click(screen.getByRole('button', { name: 'First iteration' }));
        expect(screen.queryByRole('table', { name: 'Final routing table' })).toBeNull();
        expect(screen.getByText(/has not finished at iteration 0/)).toBeInTheDocument();
        await user.click(screen.getByRole('button', { name: /Jump to the final iteration/ }));
        expect(screen.getByRole('table', { name: 'Final routing table' })).toBeInTheDocument();
    });

    it('labels unreachable destinations and shows ∞ instead of a number', async () => {
        solve.mockResolvedValue(unreachable);
        mount();
        await ready();
        const z = screen.getByTestId('rt-Z');
        expect(z).toHaveAttribute('data-status', 'unreachable');
        expect(z).toHaveTextContent('Unreachable');
        expect(z).toHaveTextContent('∞');
        expect(screen.getByText(/Z is unreachable from A/)).toBeInTheDocument();
    });
});

describe('Dijkstra lab — controls drive the experiment', () => {
    it('changing a link weight re-runs with the new weight', async () => {
        mount();
        await ready();
        solve.mockClear();
        fireEvent.change(screen.getByLabelText('Cost of B-D'), { target: { value: '10' } });
        await waitFor(() => expect(solve).toHaveBeenCalled(), { timeout: 3000 });
        expect(lastGraph().links.find(l => l.a === 'B' && l.b === 'D')!.cost).toBe(10);
        expect(lastGraph().links.every(l => !('id' in l))).toBe(true);
    });

    it('disabling a link re-runs with that link down', async () => {
        mount();
        await ready();
        solve.mockClear();
        await userEvent.setup().click(screen.getByLabelText('Link D-F enabled'));
        await waitFor(() => expect(solve).toHaveBeenCalled(), { timeout: 3000 });
        expect(lastGraph().links.find(l => l.a === 'D' && l.b === 'F')!.up).toBe(false);
    });

    it('changing the source or destination re-runs with the new endpoints', async () => {
        mount();
        await ready();
        solve.mockClear();
        fireEvent.change(screen.getByLabelText(/^Source/), { target: { value: 'C' } });
        await waitFor(() => expect(solve).toHaveBeenCalled(), { timeout: 3000 });
        expect(lastCall().slice(1)).toEqual(['C', 'F']);
        solve.mockClear();
        fireEvent.change(screen.getByLabelText(/^Destination/), { target: { value: 'E' } });
        await waitFor(() => expect(solve).toHaveBeenCalled(), { timeout: 3000 });
        expect(lastCall().slice(1)).toEqual(['C', 'E']);
    });

    it('adding and removing nodes and links edits the graph that is sent', async () => {
        mount();
        await ready();
        const user = userEvent.setup();
        solve.mockClear();
        await user.type(screen.getByLabelText('New node name'), 'G');
        await user.click(screen.getByRole('button', { name: 'Add node' }));
        await waitFor(() => expect(solve).toHaveBeenCalled(), { timeout: 3000 });
        expect(lastGraph().nodes).toContain('G');
        solve.mockClear();
        await user.click(screen.getByRole('button', { name: 'Remove node G' }));
        await user.click(screen.getByRole('button', { name: 'Remove link A-C' }));
        await waitFor(() => expect(solve).toHaveBeenCalled(), { timeout: 3000 });
        expect(lastGraph().nodes).not.toContain('G');
        expect(lastGraph().links.find(l => l.a === 'A' && l.b === 'C')).toBeUndefined();
    });

    it('does not send an invalid weight and explains why', async () => {
        mount();
        await ready();
        solve.mockClear();
        fireEvent.change(screen.getByLabelText('Cost of A-B'), { target: { value: '0' } });
        expect(await screen.findByTestId('validation')).toHaveTextContent(/cost of link A-B must be a number greater than 0/);
        fireEvent.change(screen.getByLabelText('Cost of A-B'), { target: { value: '' } });
        expect(screen.getByTestId('validation')).toBeInTheDocument();
        await new Promise(r => setTimeout(r, 500));
        expect(solve).not.toHaveBeenCalled();
        expect(screen.getByRole('button', { name: /Run Dijkstra/ })).toBeDisabled();
    });
});

describe('Dijkstra lab — stale and failed results', () => {
    it('marks results out of date when auto-recalculate is off, and re-runs on demand', async () => {
        mount();
        await ready();
        const user = userEvent.setup();
        await user.click(screen.getByLabelText(/Recalculate automatically/));
        solve.mockClear();
        fireEvent.change(screen.getByLabelText('Cost of B-D'), { target: { value: '9' } });
        const banner = await screen.findByTestId('stale');
        expect(banner).toHaveTextContent(/earlier settings/);
        expect(screen.getByTestId('results')).toHaveAttribute('data-stale', 'true');
        await new Promise(r => setTimeout(r, 500));
        expect(solve).not.toHaveBeenCalled();                     // no silent recalculation
        await user.click(screen.getByRole('button', { name: /Run Dijkstra/ }));
        await waitFor(() => expect(screen.queryByTestId('stale')).toBeNull());
        expect(solve).toHaveBeenCalledTimes(1);
    });

    it('drops a late response that belongs to inputs the user has since changed', async () => {
        let first!: (v: SolveResponse) => void;
        solve.mockReset();
        solve.mockImplementationOnce(() => new Promise<SolveResponse>(r => { first = r; }));
        solve.mockResolvedValue(unreachable);                     // answer for the newer inputs
        mount();
        await waitFor(() => expect(solve).toHaveBeenCalledTimes(1), { timeout: 3000 });
        fireEvent.change(screen.getByLabelText('Cost of B-D'), { target: { value: '8' } });
        await waitFor(() => expect(solve).toHaveBeenCalledTimes(2), { timeout: 3000 });
        await screen.findByTestId('rt-Z');                        // newer response is displayed
        await act(async () => { first(solved); });                 // old response arrives late
        expect(screen.getByTestId('rt-Z')).toBeInTheDocument();    // …and must not replace it
        expect(screen.getByTestId('step-label')).toHaveTextContent('of 6');
        expect(screen.getByText(/Z is unreachable from A/)).toBeInTheDocument();
    });

    it('shows API errors and keeps the page usable', async () => {
        solve.mockReset().mockRejectedValue(new ApiError('unknown destination node', 422));
        mount();
        expect(await screen.findByRole('alert', {}, { timeout: 3000 })).toHaveTextContent('unknown destination node');
        expect(screen.getByRole('button', { name: /Run Dijkstra/ })).toBeEnabled();
        expect(screen.queryByRole('table', { name: 'Dijkstra iteration table' })).toBeNull();
    });

    it('shows a loading state while waiting, and an empty state before any result', async () => {
        solve.mockReset().mockReturnValue(new Promise(() => {}));
        mount();
        expect(await screen.findByText(/Running Dijkstra/, {}, { timeout: 3000 })).toBeInTheDocument();
    });

    it('reports when the example topology cannot be loaded', async () => {
        vi.mocked(labsApi.routingExample).mockRejectedValue(new ApiError('Could not reach the NetRescue API', 0));
        mount();
        expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the NetRescue API');
    });
});

describe('Dijkstra lab — before/after comparison', () => {
    it('sends the chosen change and shows the backend summary and mode', async () => {
        const whatIf = {
            before: { graph: { nodes: ['A', 'F'], links: [] }, route: solved.route, alternatives: [] },
            after: { graph: { nodes: ['A', 'F'], links: [] }, route: unreachable.route, alternatives: [] },
            rerouted: true, cost_delta: null, summary: 'Route changed from X to Y.',
            execution: solved.execution,
        };
        vi.mocked(labsApi.whatIf).mockResolvedValue(whatIf as never);
        mount();
        await ready();
        const user = userEvent.setup();
        await user.click(screen.getByRole('button', { name: 'Compare before / after' }));
        expect(await screen.findByText('Route changed from X to Y.')).toBeInTheDocument();
        const args = vi.mocked(labsApi.whatIf).mock.calls[0];
        expect(args[3]).toEqual([{ kind: 'link_down', a: 'B', b: 'D' }]);
    });
});
