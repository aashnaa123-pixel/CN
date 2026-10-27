import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ExecutionProfile } from '../labs/api';
import profileFixture from './fixtures/execution_profile.json';
import exampleFixture from './fixtures/routing_example.json';
import solveFixture from './fixtures/solve_example.json';

vi.mock('../labs/api', async importOriginal => {
    const actual = await importOriginal<typeof import('../labs/api')>();
    return {
        ...actual,
        labsApi: {
            ...actual.labsApi,
            execution: vi.fn(), routingExample: vi.fn(), solve: vi.fn(),
        },
    };
});

import { labsApi } from '../labs/api';
import { resetExecutionProfileCache } from '../labs/ui';
import { JOURNEY } from '../labs/workflows';
import App from '../App';

function at(path: string) {
    return render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);
}

beforeEach(() => {
    resetExecutionProfileCache();
    // The existing pages talk to the API with fetch; make that fail fast and visibly.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network down')));
    vi.mocked(labsApi.execution).mockReset().mockResolvedValue(profileFixture as unknown as ExecutionProfile);
    vi.mocked(labsApi.routingExample).mockReset().mockResolvedValue(exampleFixture as never);
    vi.mocked(labsApi.solve).mockReset().mockResolvedValue(solveFixture as never);
});

const LAB_URLS = JOURNEY.map(j => j.path);

describe('direct navigation still works for every page', () => {
    it.each([
        ['/features/routing/playground', /Dijkstra Shortest-Path Routing/i, 'routing'],
        ['/features/tcp-congestion/playground', /TCP Congestion-Control Lab/i, 'tcp'],
        ['/features/traffic-shaping/playground', /Traffic-Shaping Lab/i, 'shaping'],
        ['/features/tcp-udp/playground', /TCP vs UDP Performance Lab/i, 'tcp-udp'],
        ['/features/integration/playground', /Detect → Reroute → Transfer/i, 'integration'],
    ])('%s', async (url, heading, id) => {
        at(url);
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(heading);
        expect(screen.queryByTestId('workflow')).toBeNull();
        expect(screen.getByTestId('journey')).toBeInTheDocument();
        await waitFor(() => expect(screen.getByTestId('mode-panel')).toHaveAttribute('data-mode'));
    });

    it.each([
        ['/features/hysteresis/playground', 'hysteresis', 'MIXED'],
        ['/features/multipath/playground', 'multipath', 'SIMULATION'],
        ['/features/diagnostics/playground', 'diagnostics', 'MIXED'],
    ])('existing playground %s opens with its execution mode and no run guide', async (url, id, mode) => {
        at(url);
        await screen.findByTestId('mode-panel');
        expect(screen.queryByTestId('workflow')).toBeNull();
        await waitFor(() => expect(screen.getByTestId('mode-panel')).toHaveAttribute('data-mode', mode));
    });

    it('unknown paths still show the not-found page', () => {
        at('/features/nothing-here');
        expect(document.body).toHaveTextContent(/not found|404|doesn.t exist|lost/i);
    });
});

describe('features listing', () => {
    it('lists four features and no longer lists the five labs as separate entries', () => {
        at('/features');
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Four/);
        const hrefs = screen.getAllByRole('link').map(a => a.getAttribute('href'));
        for (const url of LAB_URLS) expect(hrefs, url).not.toContain(url);
        for (const id of ['feature-a', 'feature-c', 'feature-d', 'feature-e']) expect(hrefs).toContain(`/features/${id}`);
        expect(screen.getByText('Network Experiment Lab')).toBeInTheDocument();
        expect(screen.queryByText('Detect → Reroute → Transfer')).toBeNull();
    });
});

describe('feature pages put the run guide before the explanation', () => {
    it.each(['feature-a', 'feature-c', 'feature-d', 'feature-e'])('%s', async id => {
        at(`/features/${id}`);
        const guide = await screen.findByTestId('detail-run-guide');
        expect(within(guide).getByTestId('workflow')).toHaveTextContent('How to run this experiment');
        // the first content section (Purpose / The journey) comes after the guide in the document
        const first = document.querySelector('[id="purpose"], [id="journey"]')!;
        expect(first).not.toBeNull();
        expect(guide.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it('existing features show their execution mode, derived from the backend', async () => {
        at('/features/feature-d');
        await waitFor(() => expect(within(screen.getByTestId('detail-run-guide')).getByTestId('mode-panel')).toHaveAttribute('data-mode', 'MIXED'));
        at('/features/feature-c');
        await waitFor(() => expect(screen.getAllByTestId('mode-panel').some(p => p.getAttribute('data-mode') === 'SIMULATION')).toBe(true));
    });

    it('the lab suite describes its five experiments in one place and starts the journey', async () => {
        at('/features/feature-e');
        expect(document.title).toBe('Network Experiment Lab — NetRescue');
        const start = await screen.findByRole('link', { name: /Start the guided journey/ });
        expect(start).toHaveAttribute('href', '/features/routing/playground');
        const journey = document.getElementById('journey')!;
        expect(journey).toHaveTextContent('Dijkstra shortest-path routing');
        expect(journey).toHaveTextContent('Mixed');
        expect(within(journey).queryAllByRole('link')).toHaveLength(0);     // described, not five more menu links
    });
});

describe('guided journey navigation', () => {
    it('walks routing → TCP congestion with Next, and back with the previous link', async () => {
        const user = userEvent.setup();
        at('/features/routing/playground');
        await screen.findByRole('table', { name: 'Dijkstra iteration table' }, { timeout: 3000 });
        await user.click(within(screen.getByTestId('journey')).getByRole('link', { name: /Next: TCP congestion/ }));
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/TCP Congestion-Control Lab/i);
        expect(screen.getByTestId('journey')).toHaveTextContent('Step 2 of 5');
        await user.click(within(screen.getByTestId('journey')).getByRole('link', { name: /← Routing/ }));
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Dijkstra/i);
    });
});
