import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ExecutionProfile } from '../labs/api';
import profileFixture from './fixtures/execution_profile.json';
import tcpFixture from './fixtures/tcp_result.json';
import shapingFixture from './fixtures/shaping_result.json';
import simFixture from './fixtures/tcpudp_sim.json';
import realFixture from './fixtures/tcpudp_real.json';
import integrationFixture from './fixtures/integration_result.json';

vi.mock('../labs/api', async importOriginal => {
    const actual = await importOriginal<typeof import('../labs/api')>();
    return {
        ...actual,
        labsApi: {
            ...actual.labsApi,
            execution: vi.fn(), tcp: vi.fn(), tcpCompare: vi.fn(), shaping: vi.fn(),
            tcpUdpSim: vi.fn(), tcpUdpReal: vi.fn(), integration: vi.fn(),
        },
    };
});

import { ApiError, labsApi } from '../labs/api';
import { resetExecutionProfileCache } from '../labs/ui';
import TcpCongestionLabPage from '../pages/TcpCongestionLabPage';
import TrafficShapingLabPage from '../pages/TrafficShapingLabPage';
import TcpUdpLabPage from '../pages/TcpUdpLabPage';
import IntegrationLabPage from '../pages/IntegrationLabPage';

const api = vi.mocked(labsApi);
const wrap = (el: React.ReactElement) => render(<MemoryRouter>{el}</MemoryRouter>);

beforeEach(() => {
    resetExecutionProfileCache();
    for (const fn of [api.tcp, api.tcpCompare, api.shaping, api.tcpUdpSim, api.tcpUdpReal, api.integration, api.execution]) fn.mockReset();
    api.execution.mockResolvedValue(profileFixture as unknown as ExecutionProfile);
    api.tcp.mockResolvedValue(tcpFixture as never);
    api.shaping.mockResolvedValue(shapingFixture as never);
    api.tcpUdpSim.mockResolvedValue(simFixture as never);
    api.tcpUdpReal.mockResolvedValue(realFixture as never);
    api.integration.mockResolvedValue(integrationFixture as never);
});

/** Every lab page: no run guide before the demo; the explanation is still present. */
function expectGuideBeforeExplanation() {
    expect(screen.queryByTestId('workflow')).toBeNull();
    expect(screen.getByRole('region', { name: 'About this experiment' })).toBeInTheDocument();
}

describe('TCP congestion lab', () => {
    it('shows its run guide, an empty state, and then a SIMULATION result with the seed used', async () => {
        wrap(<TcpCongestionLabPage />);
        expectGuideBeforeExplanation();
        expect(screen.getByText(/No result yet/)).toBeInTheDocument();
        await userEvent.setup().click(screen.getByRole('button', { name: /Run simulation/ }));
        const exec = await screen.findByTestId('execution');
        expect(exec).toHaveAttribute('data-mode', 'SIMULATION');
        expect(exec).toHaveTextContent('Random seed');
        expect(screen.getByRole('img', { name: /segments over time/ })).toBeInTheDocument();
        expect(screen.getByText('Fast retransmits')).toBeInTheDocument();
    });

    it('sends the values in the form, and a changed value changes the request', async () => {
        wrap(<TcpCongestionLabPage />);
        const user = userEvent.setup();
        fireEvent.change(screen.getByLabelText('RTT (ms)'), { target: { value: '250' } });
        fireEvent.change(screen.getByLabelText(/Random loss/), { target: { value: '0.05' } });
        fireEvent.change(screen.getByLabelText('Seed'), { target: { value: '99' } });
        await user.click(screen.getByRole('button', { name: /Run simulation/ }));
        await screen.findByTestId('execution');
        expect(api.tcp.mock.calls[0][0]).toMatchObject({ rtt_ms: 250, loss_rate: 0.05, seed: 99 });
    });

    it('marks the result out of date when an input changes after a run', async () => {
        wrap(<TcpCongestionLabPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole('button', { name: /Run simulation/ }));
        await screen.findByTestId('execution');
        expect(screen.queryByTestId('stale')).toBeNull();
        fireEvent.change(screen.getByLabelText('Router buffer (packets)'), { target: { value: '8' } });
        expect(await screen.findByTestId('stale')).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('Router buffer (packets)'), { target: { value: '40' } });
        expect(screen.queryByTestId('stale')).toBeNull();           // back to the inputs that produced it
    });

    it('presets replace the whole scenario', async () => {
        wrap(<TcpCongestionLabPage />);
        await userEvent.setup().click(screen.getByRole('button', { name: '5% random loss' }));
        expect(screen.getByLabelText(/Random loss/)).toHaveValue(0.05);
    });

    it('blocks the run when a field is empty, and shows API errors', async () => {
        wrap(<TcpCongestionLabPage />);
        fireEvent.change(screen.getByLabelText('RTT (ms)'), { target: { value: '' } });
        expect(screen.getByRole('button', { name: /Run simulation/ })).toBeDisabled();
        expect(screen.getByRole('alert')).toHaveTextContent('Every field needs a number');
        fireEvent.change(screen.getByLabelText('RTT (ms)'), { target: { value: '80' } });
        api.tcp.mockRejectedValue(new ApiError('loss_rate: Input should be less than 0.5', 422));
        await userEvent.setup().click(screen.getByRole('button', { name: /Run simulation/ }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Input should be less than 0.5');
    });

    it('compares collected scenarios and flags the comparison stale when the list changes', async () => {
        api.tcpCompare.mockResolvedValue({
            runs: [], finding: 'Highest goodput in this comparison: a.', assumptions: [],
            execution: (tcpFixture as { execution: unknown }).execution,
            table: [
                { label: 'a', rtt_ms: 80, loss_rate: 0, bandwidth_mbps: 10, goodput_mbps: 9.1, link_utilisation: 0.9, retransmissions: 3, timeouts: 0, max_cwnd_segments: 100 },
                { label: 'b', rtt_ms: 80, loss_rate: 0.05, bandwidth_mbps: 10, goodput_mbps: 0.4, link_utilisation: 0.04, retransmissions: 50, timeouts: 9, max_cwnd_segments: 15 },
            ],
        } as never);
        wrap(<TcpCongestionLabPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole('button', { name: /Add to comparison/ }));
        await user.click(screen.getByRole('button', { name: '5% random loss' }));
        await user.click(screen.getByRole('button', { name: /Add to comparison/ }));
        await user.click(screen.getByRole('button', { name: 'Run comparison' }));
        expect(await screen.findByText(/Highest goodput in this comparison/)).toBeInTheDocument();
        expect(api.tcpCompare.mock.calls[0][0]).toHaveLength(2);
        await user.click(screen.getAllByRole('button', { name: /^Remove / })[0]);
        expect(await screen.findByTestId('stale')).toBeInTheDocument();
    });
});

describe('Traffic shaping lab', () => {
    it('runs both shapers on one workload and labels the result SIMULATION', async () => {
        wrap(<TrafficShapingLabPage />);
        expectGuideBeforeExplanation();
        await userEvent.setup().click(screen.getByRole('button', { name: /Run both shapers/ }));
        expect(await screen.findByTestId('execution')).toHaveAttribute('data-mode', 'SIMULATION');
        const table = screen.getByText('Results (identical workload)').closest('section')!;
        expect(within(table).getByText('Token bucket')).toBeInTheDocument();
        expect(within(table).getByText('Leaky bucket')).toBeInTheDocument();
        expect(within(table).getByText(/Dropped packets/)).toBeInTheDocument();
        expect(screen.getByText('Queue occupancy')).toBeInTheDocument();
    });

    it('sends the pattern and parameters chosen, and shows pattern-specific controls', async () => {
        wrap(<TrafficShapingLabPage />);
        const user = userEvent.setup();
        expect(screen.getByLabelText('Packets per burst')).toBeInTheDocument();
        await user.selectOptions(screen.getByLabelText('Traffic pattern'), 'overload');
        expect(screen.queryByLabelText('Packets per burst')).toBeNull();
        expect(screen.getByLabelText('Incoming rate (bit/s)')).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('Queue capacity (packets)'), { target: { value: '7' } });
        fireEvent.change(screen.getByLabelText('Bucket depth (bytes)'), { target: { value: '12000' } });
        await user.click(screen.getByRole('button', { name: /Run both shapers/ }));
        await screen.findByTestId('execution');
        expect(api.shaping.mock.calls[0][0]).toMatchObject({ pattern: 'overload', queue_packets: 7, bucket_bytes: 12000 });
    });

    it('goes stale when a parameter changes and shows validation errors from the API', async () => {
        wrap(<TrafficShapingLabPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole('button', { name: /Run both shapers/ }));
        await screen.findByTestId('execution');
        fireEvent.change(screen.getByLabelText('Duration (s)'), { target: { value: '9' } });
        expect(screen.getByTestId('stale')).toBeInTheDocument();
        api.shaping.mockRejectedValue(new ApiError('bucket_bytes must be at least one packet', 422));
        await user.click(screen.getByRole('button', { name: /Run both shapers/ }));
        expect(await screen.findByText(/bucket_bytes must be at least one packet/)).toBeInTheDocument();
    });
});

describe('TCP vs UDP lab', () => {
    it('keeps REAL and SIMULATION results apart and labels each from the backend', async () => {
        wrap(<TcpUdpLabPage />);
        expectGuideBeforeExplanation();
        await waitFor(() => expect(screen.getByTestId('mode-panel')).toHaveAttribute('data-mode', 'MIXED'));
        const user = userEvent.setup();

        await user.click(screen.getByRole('button', { name: /Run real experiment/ }));
        await waitFor(() => expect(screen.getByTestId('execution')).toHaveAttribute('data-mode', 'REAL'));
        expect(screen.getByTestId('execution')).toHaveTextContent('127.0.0.1');
        expect(screen.getByTestId('execution')).toHaveTextContent('REAL NETWORK OPERATION');
        expect(screen.getByText('connect()')).toBeInTheDocument();

        await user.click(screen.getByRole('tab', { name: 'Simulation' }));
        // the real result must not be shown under the simulation tab
        expect(screen.queryByText('connect()')).toBeNull();
        expect(screen.getByText(/No simulation result yet/)).toBeInTheDocument();
        await user.click(screen.getByRole('button', { name: /Run simulation/ }));
        await waitFor(() => expect(screen.getByTestId('execution')).toHaveAttribute('data-mode', 'SIMULATION'));
        expect(screen.getByTestId('execution')).toHaveTextContent('Random seed');
        expect(screen.queryByText('REAL NETWORK OPERATION')).toBeNull();

        await user.click(screen.getByRole('tab', { name: 'Real loopback sockets' }));
        expect(screen.getByTestId('execution')).toHaveAttribute('data-mode', 'REAL');   // still its own result
    });

    it('sends the settings of the selected mode only', async () => {
        wrap(<TcpUdpLabPage />);
        const user = userEvent.setup();
        fireEvent.change(screen.getByLabelText('TCP bulk (bytes)'), { target: { value: '500000' } });
        await user.click(screen.getByRole('button', { name: /Run real experiment/ }));
        await screen.findByTestId('execution');
        expect(api.tcpUdpReal.mock.calls[0][0]).toMatchObject({ bulk_bytes: 500000 });
        expect(api.tcpUdpSim).not.toHaveBeenCalled();

        await user.click(screen.getByRole('tab', { name: 'Simulation' }));
        fireEvent.change(screen.getByLabelText('Random loss'), { target: { value: '0.08' } });
        fireEvent.change(screen.getByLabelText('UDP send rate (Mbit/s)'), { target: { value: '12' } });
        await user.click(screen.getByRole('button', { name: /Run simulation/ }));
        await waitFor(() => expect(api.tcpUdpSim).toHaveBeenCalled());
        expect(api.tcpUdpSim.mock.calls[0][0]).toMatchObject({ loss_rate: 0.08, udp_send_rate_mbps: 12 });
    });

    it('does not offer loss or delay controls for the real mode', () => {
        wrap(<TcpUdpLabPage />);
        expect(screen.queryByLabelText(/loss/i)).toBeNull();
        expect(screen.queryByLabelText(/RTT/i)).toBeNull();
        expect(screen.getByText(/no\s+latency, bandwidth limit or normal packet loss/i)).toBeInTheDocument();
    });

    it('reports a failed real run honestly', async () => {
        api.tcpUdpReal.mockRejectedValue(new ApiError('a real-socket experiment is already running; try again shortly', 409));
        wrap(<TcpUdpLabPage />);
        await userEvent.setup().click(screen.getByRole('button', { name: /Run real experiment/ }));
        expect(await screen.findByRole('alert')).toHaveTextContent('already running');
        expect(screen.queryByTestId('execution')).toBeNull();
    });
});

describe('Integration lab', () => {
    it('runs, shows detector → route → transfer sections and labels the whole run SIMULATION', async () => {
        wrap(<IntegrationLabPage />);
        expectGuideBeforeExplanation();
        await userEvent.setup().click(screen.getByRole('button', { name: /Run experiment/ }));
        expect(await screen.findByTestId('execution')).toHaveAttribute('data-mode', 'SIMULATION');
        expect(screen.getByText('1 · Hysteresis detector on link B-D')).toBeInTheDocument();
        expect(screen.getByText('2 · Route recalculated by Dijkstra')).toBeInTheDocument();
        expect(screen.getByText(/3 · Same transfer over each route/)).toBeInTheDocument();
        expect(screen.getAllByText('Baseline (healthy)').length).toBeGreaterThan(0);
        expect(screen.getAllByText('Degraded, not rerouted').length).toBeGreaterThan(0);
        expect(screen.getAllByText('After reroute').length).toBeGreaterThan(0);
        expect(screen.getByRole('button', { name: 'Export JSON' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Export CSV/ })).toBeInTheDocument();
        expect(screen.getByText(/Where the models connect/)).toBeInTheDocument();
    });

    it('sends the thresholds and outage chosen, and goes stale when they change', async () => {
        wrap(<IntegrationLabPage />);
        const user = userEvent.setup();
        fireEvent.change(screen.getByLabelText('n (→ FAILED)'), { target: { value: '9' } });
        fireEvent.change(screen.getByLabelText('Outage length (ticks)'), { target: { value: '3' } });
        await user.click(screen.getByRole('button', { name: /Run experiment/ }));
        await screen.findByTestId('execution');
        expect(api.integration.mock.calls[0][0]).toMatchObject({ n: 9, outage_len: 3 });
        fireEvent.change(screen.getByLabelText('f_thresh (→ SUSPECT)'), { target: { value: '2' } });
        expect(screen.getByTestId('stale')).toBeInTheDocument();
    });

    it('shows validation errors returned by the API', async () => {
        api.integration.mockRejectedValue(new ApiError('thresholds must satisfy 1 <= f_thresh < n and r_thresh >= 1', 422));
        wrap(<IntegrationLabPage />);
        await userEvent.setup().click(screen.getByRole('button', { name: /Run experiment/ }));
        expect(await screen.findByRole('alert')).toHaveTextContent('thresholds must satisfy');
    });
});
