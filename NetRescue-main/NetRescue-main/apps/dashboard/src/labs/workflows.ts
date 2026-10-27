/**
 * Step-by-step "how to run this experiment" flows, one per experiment.
 *
 * Every flow has the same five beats — set up, configure, act, inspect,
 * change-and-compare — but the wording is specific to the experiment. Only
 * experiments that exist in this build are listed.
 */
export interface WorkflowStep { title: string; text: string }
export interface Workflow { id: string; name: string; steps: WorkflowStep[] }

export const WORKFLOWS: Record<string, Workflow> = {
    hysteresis: {
        id: 'hysteresis',
        name: 'Hysteresis failure detection & recovery',
        steps: [
            { title: 'Build your overlay', text: 'Draw nodes and links on the canvas (or load the example), then press Apply to start one real loopback listener per node.' },
            { title: 'Set the detector', text: 'Choose the heartbeat interval and the thresholds f_thresh (→ SUSPECT), n (→ FAILED) and r_thresh (→ HEALTHY); EWMA is optional.' },
            { title: 'Break something', text: 'Fail a node or a link, then send a test packet from a source to a destination.' },
            { title: 'Inspect the result', text: 'Read the detector states, the transitions the nodes really made, the new route and whether the destination acknowledged the packet.' },
            { title: 'Change and compare', text: 'Restore the element or retune a threshold, repeat, then run the hysteresis-vs-naive replay to compare route changes.' },
        ],
    },
    multipath: {
        id: 'multipath',
        name: 'Multipath file transfer & integrity verification',
        steps: [
            { title: 'Choose a file', text: 'Upload a file (held in memory only). It is split into fixed-size chunks.' },
            { title: 'Configure the paths', text: 'Set each logical path’s delay, bandwidth estimate and loss, plus the scheduler and retransmission policy.' },
            { title: 'Start the transfer', text: 'Press Start and watch chunks leave on the modelled paths, retry and arrive.' },
            { title: 'Inspect the result', text: 'Compare the sender and receiver SHA-256: a transfer is complete only if they match. Check per-path counters and the event log.' },
            { title: 'Change and compare', text: 'Raise a path’s loss or switch the scheduler, run again with the same seed, and compare completion time and retransmissions.' },
        ],
    },
    diagnostics: {
        id: 'diagnostics',
        name: 'Network diagnostics & benchmarking',
        steps: [
            { title: 'Pick a target', text: 'Use the default target or enter a hostname or IPv4 address to measure.' },
            { title: 'Choose what to run', text: 'Real probes (ping, route trace, DNS, adapters) are separate from simulated ones (fault scenarios, hysteresis sweep, scheduler comparison).' },
            { title: 'Run it', text: 'Press Run Diagnostics for the live probes, or start a benchmark, fault scenario or sweep from its own panel.' },
            { title: 'Inspect the result', text: 'Read latency, loss, hops, resolved addresses and adapters; each section states whether it was measured or simulated and shows any error or timeout.' },
            { title: 'Change and compare', text: 'Repeat with another target, seed or ratio and compare runs or export the report as CSV or JSON.' },
        ],
    },
    routing: {
        id: 'routing',
        name: 'Dijkstra shortest-path routing',
        steps: [
            { title: 'Build your network', text: 'Add nodes and connect them with weighted links; each cost must be greater than 0.' },
            { title: 'Choose a source', text: 'Select the node that shortest paths are computed from, and a destination to highlight.' },
            { title: 'Run the algorithm', text: 'Run Dijkstra (or leave auto-recalculate on) and step through the iterations with Previous and Next.' },
            { title: 'Inspect the results', text: 'Compare the iteration table, the final routing table (next hop, cost, full path) and the highlighted route on the topology.' },
            { title: 'Introduce a change', text: 'Edit a link weight or disable a link and watch the tables and route update, or use the before/after comparison.' },
        ],
    },
    tcp: {
        id: 'tcp',
        name: 'TCP congestion control',
        steps: [
            { title: 'Choose a scenario', text: 'Start from a preset or set RTT, bandwidth, random loss and router buffer size.' },
            { title: 'Set the sender', text: 'Pick the initial cwnd, ssthresh, duration and seed (the same seed reproduces the run exactly).' },
            { title: 'Run the simulation', text: 'Press Run to simulate one TCP Reno flow.' },
            { title: 'Inspect the result', text: 'Read the cwnd/ssthresh chart and phase strip, goodput over time, and the loss events (fast retransmit, timeout, drops).' },
            { title: 'Change and compare', text: 'Add scenarios to the comparison (different RTT, loss or bandwidth) and compare goodput and retransmissions.' },
        ],
    },
    shaping: {
        id: 'shaping',
        name: 'QoS & traffic shaping',
        steps: [
            { title: 'Pick a traffic pattern', text: 'Choose steady, bursts, overload or random (Poisson) arrivals.' },
            { title: 'Configure the shapers', text: 'Set the shaping rate, token-bucket depth, queue capacity, packet size and duration.' },
            { title: 'Run both shapers', text: 'Press Run: the same arrival list goes through a token bucket and a leaky bucket.' },
            { title: 'Inspect the result', text: 'Compare arrivals vs departures, queue occupancy, token level, delay, throughput and drops.' },
            { title: 'Change and compare', text: 'Deepen the bucket or shrink the queue and re-run to see how bursts, delay and drops move.' },
        ],
    },
    'tcp-udp': {
        id: 'tcp-udp',
        name: 'TCP vs UDP',
        steps: [
            { title: 'Pick a mode', text: 'Real loopback sockets measure this machine; Simulation models a lossy path. They are separate and labelled.' },
            { title: 'Configure it', text: 'Real: round trips, payload and transfer sizes. Simulation: RTT, bandwidth, loss, transfer size and the UDP send rate.' },
            { title: 'Run the experiment', text: 'Press Run; real mode opens sockets on 127.0.0.1 for about a second.' },
            { title: 'Inspect the result', text: 'Compare connect time, latency, throughput and delivery for each protocol, plus the stated method and limitations.' },
            { title: 'Change and compare', text: 'Change the loss or UDP rate in simulation, or the sizes in real mode, and re-run; no general winner is implied.' },
        ],
    },
    integration: {
        id: 'integration',
        name: 'Detect → reroute → transfer',
        steps: [
            { title: 'Define the outage', text: 'Set when the monitored link B-D starts failing and for how many heartbeat ticks.' },
            { title: 'Tune detection and routing', text: 'Set f_thresh, n, r_thresh, the SUSPECT cost multiplier and how lossy the degraded link is.' },
            { title: 'Run the experiment', text: 'Press Run: the detector drives the routing graph, Dijkstra recalculates, and the same transfer is simulated on each route.' },
            { title: 'Inspect the result', text: 'Follow the detector strip, the route timeline, and the baseline / degraded / rerouted transfer metrics.' },
            { title: 'Change and compare', text: 'Shorten the outage or raise n to see hysteresis suppress rerouting; export the results as JSON or CSV.' },
        ],
    },
    suite: {
        id: 'suite',
        name: 'Network Experiment Lab (guided journey)',
        steps: [
            { title: 'Start with routing', text: 'Build a graph and see how Dijkstra chooses routes and fills a routing table.' },
            { title: 'Look at transport', text: 'Run the TCP congestion simulation to see how a sender reacts to RTT and loss.' },
            { title: 'Add traffic shaping', text: 'Compare token and leaky buckets on the same workload.' },
            { title: 'Compare TCP and UDP', text: 'Measure real loopback sockets or simulate a lossy path.' },
            { title: 'Combine them', text: 'Run the integration experiment: detect a degraded link, reroute, and compare transfer metrics before and after.' },
        ],
    },
};

/** The guided journey: the five experiments of the Network Experiment Lab, in order. */
export const JOURNEY: { id: string; path: string; label: string }[] = [
    { id: 'routing', path: '/features/routing/playground', label: 'Routing' },
    { id: 'tcp', path: '/features/tcp-congestion/playground', label: 'TCP congestion' },
    { id: 'shaping', path: '/features/traffic-shaping/playground', label: 'Traffic shaping' },
    { id: 'tcp-udp', path: '/features/tcp-udp/playground', label: 'TCP vs UDP' },
    { id: 'integration', path: '/features/integration/playground', label: 'Integration' },
];

/** Which execution-profile feature a detail page describes (the suite spans several, so it has none). */
export const FEATURE_MODE: Record<string, string> = {
    'feature-a': 'hysteresis',
    'feature-c': 'multipath',
    'feature-d': 'diagnostics',
};

/** Which workflow belongs to which feature page. */
export const FEATURE_WORKFLOW: Record<string, string> = {
    'feature-a': 'hysteresis',
    'feature-c': 'multipath',
    'feature-d': 'diagnostics',
    'feature-e': 'suite',
};
