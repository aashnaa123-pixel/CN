import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ExecutionInfo, ExecutionProfile } from '../labs/api';
import profileFixture from './fixtures/execution_profile.json';
import tcpFixture from './fixtures/tcp_result.json';
import realFixture from './fixtures/tcpudp_real.json';

vi.mock('../labs/api', async importOriginal => {
    const actual = await importOriginal<typeof import('../labs/api')>();
    return { ...actual, labsApi: { ...actual.labsApi, execution: vi.fn() } };
});

import { labsApi } from '../labs/api';
import {
    Badge, ExecutionBadge, ExperimentWorkflow, FeatureModePanel, JourneyBar, StaleBanner, resetExecutionProfileCache,
    useLabResult,
} from '../labs/ui';

const profile = profileFixture as unknown as ExecutionProfile;
const simExec = (tcpFixture as unknown as { execution: ExecutionInfo }).execution;
const realExec = (realFixture as unknown as { execution: ExecutionInfo }).execution;

beforeEach(() => {
    resetExecutionProfileCache();
    vi.mocked(labsApi.execution).mockReset();
    vi.mocked(labsApi.execution).mockResolvedValue(profile);
});

describe('execution badges (modes come from the backend response)', () => {
    it('labels a real-socket result REAL NETWORK OPERATION and shows what was executed', () => {
        render(<ExecutionBadge execution={realExec} />);
        const box = screen.getByTestId('execution');
        expect(box).toHaveAttribute('data-mode', 'REAL');
        expect(within(box).getByText('REAL NETWORK OPERATION')).toBeInTheDocument();
        expect(box).toHaveTextContent('127.0.0.1');
        expect(box).toHaveTextContent(/sockets/i);
        expect(box).toHaveTextContent(realExec.evidence);
    });

    it('labels a model result SIMULATION and shows its seed, model and limitations', () => {
        render(<ExecutionBadge execution={simExec} />);
        const box = screen.getByTestId('execution');
        expect(box).toHaveAttribute('data-mode', 'SIMULATION');
        expect(within(box).getByText('SIMULATION')).toBeInTheDocument();
        expect(box).toHaveTextContent('Random seed');
        expect(box).toHaveTextContent('3');
        expect(box).toHaveTextContent(/TCP Reno/);
        expect(box).toHaveTextContent(/Not a kernel experiment/);
    });

    it('does not claim a mode when the backend reported none, and flags unverified ones', () => {
        const { rerender } = render(<ExecutionBadge execution={undefined} />);
        expect(screen.getByTestId('execution')).toHaveTextContent(/not reported/i);
        expect(screen.queryByText('REAL NETWORK OPERATION')).toBeNull();
        rerender(<ExecutionBadge execution={{ ...simExec, mode: 'UNVERIFIED' }} />);
        expect(screen.getByTestId('execution')).toHaveAttribute('data-mode', 'UNVERIFIED');
    });

    it('Badge short forms map measurement→REAL and simulation→SIMULATED', () => {
        render(<><Badge kind="measurement" /><Badge kind="simulation" /><Badge kind="MIXED" /></>);
        expect(screen.getByText('REAL')).toBeInTheDocument();
        expect(screen.getByText('SIMULATED')).toBeInTheDocument();
        expect(screen.getByText('MIXED')).toBeInTheDocument();
    });
});

describe('feature mode panel', () => {
    it('shows MIXED for TCP vs UDP with both operations listed and labelled', async () => {
        render(<FeatureModePanel feature="tcp-udp" />);
        await waitFor(() => expect(screen.getByTestId('mode-panel')).toHaveAttribute('data-mode', 'MIXED'));
        const panel = screen.getByTestId('mode-panel');
        expect(panel).toHaveTextContent('TCP and UDP loopback experiment');
        expect(panel).toHaveTextContent('TCP vs UDP model');
        expect(within(panel).getAllByText('REAL').length).toBeGreaterThan(0);
        expect(within(panel).getAllByText('SIMULATED').length).toBeGreaterThan(0);
        expect(panel).toHaveTextContent(/verified by the backend/i);
    });

    it('shows SIMULATION for routing and does not list any real operation', async () => {
        render(<FeatureModePanel feature="routing" />);
        await waitFor(() => expect(screen.getByTestId('mode-panel')).toHaveAttribute('data-mode', 'SIMULATION'));
        const panel = screen.getByTestId('mode-panel');
        expect(panel.querySelectorAll('li [data-mode="REAL"]')).toHaveLength(0);
        expect(panel.querySelectorAll('li [data-mode="SIMULATION"]').length).toBeGreaterThan(0);
    });

    it('reports when the mode cannot be fetched instead of guessing', async () => {
        vi.mocked(labsApi.execution).mockRejectedValue(new Error('API down'));
        render(<FeatureModePanel feature="routing" />);
        expect(await screen.findByText(/Execution mode unavailable: API down/)).toBeInTheDocument();
    });

    it('shows a loading state first', () => {
        vi.mocked(labsApi.execution).mockReturnValue(new Promise(() => {}));
        render(<FeatureModePanel feature="routing" />);
        expect(screen.getByText(/Checking execution mode/)).toBeInTheDocument();
    });
});

describe('workflow, journey and stale results', () => {
    it('renders the five specific steps for an experiment', () => {
        render(<ExperimentWorkflow id="routing" />);
        const wf = screen.getByTestId('workflow');
        expect(within(wf).getByText('How to run this experiment')).toBeInTheDocument();
        expect(within(wf).getAllByText(/^Step \d$/)).toHaveLength(5);
        expect(wf).toHaveTextContent('Build your network');
        expect(wf).toHaveTextContent('Introduce a change');
    });

    it('renders nothing for an unknown experiment id', () => {
        const { container } = render(<ExperimentWorkflow id="nope" />);
        expect(container).toBeEmptyDOMElement();
    });

    it('journey bar shows position and links to the neighbouring steps only', () => {
        render(<MemoryRouter><JourneyBar current="shaping" /></MemoryRouter>);
        const nav = screen.getByTestId('journey');
        expect(nav).toHaveTextContent('Step 3 of 5 · Traffic shaping');
        expect(within(nav).getByRole('link', { name: /TCP congestion/ })).toHaveAttribute('href', '/features/tcp-congestion/playground');
        expect(within(nav).getByRole('link', { name: /TCP vs UDP/ })).toHaveAttribute('href', '/features/tcp-udp/playground');
        expect(within(nav).getAllByRole('link')).toHaveLength(2);   // no five-link menu
    });

    it('first and last steps have only one neighbour', () => {
        const { rerender } = render(<MemoryRouter><JourneyBar current="routing" /></MemoryRouter>);
        expect(within(screen.getByTestId('journey')).getAllByRole('link')).toHaveLength(1);
        rerender(<MemoryRouter><JourneyBar current="integration" /></MemoryRouter>);
        expect(within(screen.getByTestId('journey')).getAllByRole('link')).toHaveLength(1);
    });

    it('marks a result stale as soon as its inputs change', async () => {
        function Probe() {
            const [v, setV] = React.useState(1);
            const r = useLabResult<string>(String(v));
            return (
                <div>
                    <button onClick={() => r.set('result', String(v))}>run</button>
                    <button onClick={() => setV(v + 1)}>change</button>
                    {r.result && <span data-testid="res">{r.result}</span>}
                    <StaleBanner stale={r.stale} />
                </div>
            );
        }
        render(<Probe />);
        const user = userEvent.setup();
        expect(screen.queryByTestId('stale')).toBeNull();
        await user.click(screen.getByText('run'));
        expect(screen.getByTestId('res')).toBeInTheDocument();
        expect(screen.queryByTestId('stale')).toBeNull();
        await user.click(screen.getByText('change'));
        expect(screen.getByTestId('stale')).toBeInTheDocument();
        await user.click(screen.getByText('run'));
        expect(screen.queryByTestId('stale')).toBeNull();
    });
});
