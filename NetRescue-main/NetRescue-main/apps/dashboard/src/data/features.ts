export type FeatureId = 'feature-a' | 'feature-c' | 'feature-d' | 'feature-e';

export interface FeatureSection {
    id: string;
    heading: string;
    body?: string;
    bullets?: string[];
    table?: {
        columns: string[];
        rows: string[][];
    };
    /** YouTube video id, rendered below the section. Absent means no embed. */
    videoId?: string;
    /** Exact video title, used as the caption and the iframe's accessible name. */
    videoTitle?: string;
    /** Channel name, shown after the title. */
    videoAuthor?: string;
}

export interface Feature {
    id: FeatureId;
    index: string;
    title: string;
    tagline: string;
    heroSub: string;
    watermark: string;
    heroVideoUrl: string;
    highlights: string[];
    /** Interactive lab for this feature, when it has one. */
    lab?: { to: string; label: string };
    sections: FeatureSection[];
    researchAnchor: string[];
}

const VIDEO_URLS = {
    hero: 'https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260622_083515_290e5a10-0b95-41af-a5e2-32b6389baa4d.mp4',
    cinematic: 'https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260622_092455_089c54f8-3b03-4966-9df1-e9746063d0ef.mp4',
    metrics: 'https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260622_095810_ecea3dd2-fc5e-4e41-8696-4219290b6589.mp4',
    features: 'https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260622_095750_32a52ce0-2005-45c9-9093-41f03fde9530.mp4',
    footer: 'https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260622_080203_fd7f4f85-3a86-4837-8192-85e7bfe68e75.mp4',
};

export { VIDEO_URLS };

export const features: Feature[] = [
    {
        id: 'feature-a',
        index: 'A',
        title: 'Hysteresis Recovery',
        tagline: 'Three-state detection with a dead band that eliminates route flapping.',
        heroSub: 'Maintain overlay connectivity when a forwarding process or link becomes unavailable, while avoiding route flapping caused by oscillation around a single detection threshold.',
        watermark: 'A',
        heroVideoUrl: VIDEO_URLS.cinematic,
        highlights: [
            'Three-state hysteresis: HEALTHY → SUSPECT → FAILED with configurable dead band',
            'Based on Maltz thesis (CMU) §7.6.1 and VNT control literature',
            'Target: route flap reduction ≥ 50% at hysteresis ratio ≥ 2.0 (check with the lab)',
        ],
        sections: [
            {
                id: 'purpose',
                heading: 'Purpose',
                body: 'Maintain overlay connectivity when a forwarding process or configured link becomes unavailable, while avoiding route flapping caused by oscillation around a single detection threshold. Naïve threshold detection declares a link healthy above a threshold and failed below it. When quality oscillates around that threshold, the system repeatedly declares failure and recovery, causing routing instability.',
            },
            {
                id: 'specification',
                heading: 'Specification',
                body: 'The hysteresis state machine uses two distinct thresholds: a lower threshold to enter the failed state, and a higher threshold to return to the healthy state. The gap between them is a dead band that absorbs oscillations.',
                table: {
                    columns: ['Transition', 'Condition', 'Default'],
                    rows: [
                        ['HEALTHY → SUSPECT', 'Consecutive missed heartbeats ≥ F_thresh', '3'],
                        ['SUSPECT → FAILED', 'Consecutive missed heartbeats ≥ N', '5'],
                        ['FAILED → HEALTHY', 'Consecutive successful heartbeats ≥ R_thresh', '5'],
                        ['Dead band', 'R_thresh > F_thresh', 'ratio ≥ 1.5'],
                    ],
                },
                bullets: [
                    'No direct HEALTHY ↔ FAILED transitions; all transitions pass through SUSPECT',
                    'F_thresh, N, R_thresh, and heartbeat interval are configurable at run start',
                    'hysteresis_ratio = R_thresh / F_thresh is a primary experimental variable',
                    'Optional EWMA filter on heartbeat quality to accelerate convergence',
                    'Hop limits, route versioning, and stale-route rejection prevent loops',
                ],
                videoId: 'VpS8eFMRTjw',
                videoTitle: 'Hysteresis Recovery',
            },
            {
                id: 'acceptance',
                heading: 'Acceptance Criteria',
                bullets: [
                    'With an alternate route, traffic resumes after a forwarding process is terminated',
                    'Without an alternate route, the system reports unreachable rather than claiming recovery',
                    'Repeated fail/restart tests do not create a persistent forwarding loop',
                    'Sweep hysteresis_ratio ∈ {1.0, 1.5, 2.0, 2.5, 3.0} produces a trade-off curve between route flap count and recovery time',
                    'Recovery time and sent/received message counts are recorded per run',
                ],
            },
            {
                id: 'research',
                heading: 'Research Anchor',
                body: 'The hysteresis approach is grounded in published research on overlay and ad hoc network routing stability.',
            },
        ],
        researchAnchor: [
            'Maltz thesis (CMU), §7.6.1 — Route hysteresis for ad hoc/overlay networks',
            'VNT control literature — Two-state utilization hysteresis',
            'Overlay routing stability research — Hysteresis-based layered traffic engineering',
        ],
    },
    {
        id: 'feature-c',
        index: 'C',
        title: 'Multipath Transfer',
        tagline: 'DB-DSP scheduling and RTX-CL retransmission across heterogeneous paths.',
        heroSub: 'Transfer a file in chunks across multiple logical overlay paths, reconstruct it, verify integrity, and compare published scheduling algorithms against baselines.',
        watermark: 'C',
        heroVideoUrl: VIDEO_URLS.metrics,
        highlights: [
            'DB-DSP delay-based scheduling with EWMA smoothing on forward-delay and throughput',
            'RTX-CL retransmission: selects path by cwnd + loss, not delay alone',
            'SHA-256 verified integrity. Bounded failure — no silent data corruption',
        ],
        sections: [
            {
                id: 'purpose',
                heading: 'Purpose',
                body: 'Transfer a file in chunks across multiple logical overlay paths, reconstruct it, verify integrity, and compare a published delay-based scheduler (DB-DSP) and retransmission policy (RTX-CL) against baselines under identical emulated conditions.',
            },
            {
                id: 'specification',
                heading: 'Specification',
                body: 'The DB-CMT approach addresses out-of-order arrival with three components: DB-DSP (delay-based scheduling), RTX-CL (loss-and-delay-aware retransmission), and DB-FRP (fast retransmission).',
                table: {
                    columns: ['Variant', 'Rule', 'Role'],
                    rows: [
                        ['DB-DSP', 'Send on path minimising estimated arrival time', 'Primary algorithm'],
                        ['Round-Robin', 'Cycle chunks across paths in order', 'Naïve baseline'],
                        ['Weighted RR', 'Allocate proportional to bandwidth', 'Bandwidth-aware baseline'],
                        ['Single-Path TCP', 'Use only lowest-RTT path', 'Lower bound'],
                        ['Naïve Delay-Aware', 'Use only forward delay, ignore throughput', 'DB-DSP ablation'],
                    ],
                },
                bullets: [
                    'Estimate forward delay FD_i from RTT; smooth with EWMA',
                    'Estimate throughput TP_i from cwnd_i / RTT_i; smooth with EWMA',
                    'RTX-CL: on retransmission, select path with larger cwnd when delays are similar',
                    'Bounded retry policy per chunk and per transfer',
                    'Path failure → remove from candidate set and re-route in-flight chunks',
                ],
                videoId: 'tzcv2vwYWcQ',
                videoTitle: 'Multipath Transfer',
            },
            {
                id: 'acceptance',
                heading: 'Acceptance Criteria',
                bullets: [
                    'SHA-256 match between source and receiver on successful transfer',
                    'Missing, duplicate, and out-of-order chunks handled per documented protocol',
                    'Interrupting one path does not corrupt the file',
                    'DB-DSP achieves ≤ 50% out-of-order count vs round-robin under 5:1 heterogeneity',
                    'RTX-CL achieves ≤ 90% of RTX-CWND transfer time under 5% loss',
                    'All comparison runs repeated ≥ 5 times with median and p95 reported',
                ],
            },
            {
                id: 'research',
                heading: 'Research Anchor',
                body: 'DB-CMT is a published multipath transfer protocol with delay-based scheduling validated in simulation.',
            },
        ],
        researchAnchor: [
            'Verma et al., J. Network and Systems Management, 2022 — DB-CMT / DB-DSP / RTX-CL',
            'CMT-SCTP literature — Delay-based chunk scheduling reduces reordering',
            'Liu et al., IEEE, 2008 — Receive buffer blocking',
        ],
    },
    {
        id: 'feature-d',
        index: 'D',
        title: 'Network Diagnostics',
        tagline: 'Real Windows measurements plus a deterministic fault simulator — no Linux kernel required.',
        heroSub: 'Measure this machine\'s network for real (latency, packet loss, route, DNS, adapters), run repeatable benchmarks, and reproduce fault behaviour with a seeded simulator. It replaces the Linux tc/netem emulator and acts as the measurement apparatus for Features A and B.',
        watermark: 'D',
        heroVideoUrl: VIDEO_URLS.features,
        highlights: [
            'Real ping/tracert/DNS/adapter measurements using the operating system\'s own tools',
            'Seeded fault simulator: unreachable, latency/jitter, packet loss, timeout, intermittent',
            'Every number labelled real, controlled, simulated or unsupported — no fake metrics',
        ],
        sections: [
            {
                id: 'purpose',
                heading: 'Purpose',
                body: 'Measure network behaviour on Windows 10/11 without the Linux kernel, a virtualization layer or administrator rights, and serve as the measurement apparatus for Features A and B. The previous tc/netem emulator was a Linux-only facility; it is replaced by real operating-system probes plus a deterministic software simulator, and the difference is stated everywhere.',
            },
            {
                id: 'specification',
                heading: 'Specification',
                body: 'Each capability is either a real measurement of this host, a controlled benchmark over modelled conditions, or explicitly unsupported. Nothing is presented as a real measurement when it is not.',
                table: {
                    columns: ['Capability', 'What it does', 'Provenance'],
                    rows: [
                        ['Ping', 'Latency (min/avg/max/stddev) and packet loss, per probe', 'Real — Windows ping'],
                        ['Route trace', 'Hop-by-hop path to a target', 'Real — Windows tracert'],
                        ['DNS', 'Name resolution and how long it took', 'Real — OS resolver'],
                        ['Adapters / IP', 'Link state, IPv4 configuration, gateway, DNS servers', 'Real — PowerShell Get-Net*'],
                        ['Benchmark runner', 'Repeatable measurements, history, comparison, CSV/JSON export', 'Real measurement'],
                        ['Fault simulator', 'Unreachable, jitter, loss, timeout, intermittent, transitions', 'Simulated — seeded'],
                        ['Hysteresis sweep', 'Ratio 1.0–3.0 stability/recovery trade-off curve', 'Controlled'],
                        ['Scheduler comparison', 'Scheduler variants under identical declared paths', 'Controlled'],
                        ['tc/netem shaping', 'Kernel-level delay/jitter/loss/reorder', 'Unsupported on Windows'],
                    ],
                },
                bullets: [
                    'Subprocesses are bounded, arguments are passed as arrays (never a shell string), and inputs are validated against injection',
                    'Real probes run off the event loop, so a slow command never blocks the API',
                    'A ping with zero replies is a valid result, not an error; the response says "unreachable"',
                    'No interface, route or firewall rule is modified — no privileged action is required or taken',
                    'Configuration is normalised and hashed (SHA-256) into every report with the software version and seed',
                ],
                videoId: 'gUJLCU58bcA',
                videoTitle: 'Network Diagnostics',
            },
            {
                id: 'acceptance',
                heading: 'Acceptance Criteria',
                bullets: [
                    'Runs on Windows 10/11 without Ubuntu, WSL2, kernel modules, tc, netem or admin rights',
                    'Reports include units, target, repetition count, environment, config hash and limitations',
                    'Real measurements, controlled benchmarks and simulated faults are visually and textually distinguished',
                    'Feature A can bracket an experiment with a real before/after baseline and compare them',
                    'Feature B transfers can be paired with a diagnostics run, shown side by side and labelled',
                    'Hysteresis sweep covers ratios 1.0–3.0 and provides a trade-off curve plus a naive baseline',
                    'Unavailable commands, DNS failures, unreachable targets, permission errors and timeouts degrade visibly rather than silently',
                ],
            },
            {
                id: 'research',
                heading: 'Research Anchor',
                body: 'The replacement rests on Microsoft\'s documented Windows network commands and on the published research the original emulation supported. tc/netem is named as the Linux-only facility it was, not as something this build uses.',
            },
        ],
        researchAnchor: [
            'Microsoft Learn — ping command: https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/ping',
            'Microsoft Learn — tracert command: https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/tracert',
            'Microsoft Learn — Get-NetAdapter: https://learn.microsoft.com/en-us/powershell/module/netadapter/get-netadapter',
            'Microsoft Learn — Get-NetIPConfiguration: https://learn.microsoft.com/en-us/powershell/module/nettcpip/get-netipconfiguration',
            'Linux tc-netem(8): https://man7.org/linux/man-pages/man8/tc-netem.8.html',
            'Maltz thesis (CMU) §7.6.1 — Route hysteresis (the apparatus serves Feature A)',
            'Verma et al., J. Network and Systems Management, 2022 — DB-CMT (the apparatus serves Feature B)',
        ],
    },
    {
        id: 'feature-e',
        index: 'E',
        title: 'Network Experiment Lab',
        tagline: 'One guided journey: routing, TCP congestion, traffic shaping, TCP vs UDP, and a combined failure-recovery experiment.',
        heroSub: 'Five connected experiments you run in order. Build a network and compute its routes with Dijkstra, watch TCP react to loss, compare traffic shapers, compare TCP with UDP, then combine detection, rerouting and transfer in one run. Every result says whether it is a real network operation or a simulation.',
        watermark: 'E',
        heroVideoUrl: VIDEO_URLS.cinematic,
        lab: { to: '/features/routing/playground', label: 'Start the guided journey' },
        highlights: [
            'Dijkstra with a step-through iteration table and a final routing table (next hop, cost, full path)',
            'TCP Reno congestion window, token vs leaky bucket shaping, and TCP vs UDP (real loopback sockets or simulation)',
            'Detect → reroute → transfer: the hysteresis detector, Dijkstra and the TCP model in one experiment',
        ],
        sections: [
            {
                id: 'journey',
                heading: 'The journey',
                body: 'Each step has its own page with a short run guide at the top, and a Next button to the following step. Steps can be run on their own or in order.',
                table: {
                    columns: ['Step', 'Experiment', 'Execution mode', 'What it shows'],
                    rows: [
                        ['1', 'Dijkstra shortest-path routing', 'Simulation', 'Per-iteration distances and predecessors, the final routing table, the effect of a weight change or a disabled link'],
                        ['2', 'TCP congestion control', 'Simulation', 'cwnd, ssthresh and goodput of one TCP Reno flow under chosen RTT, loss, buffer and bandwidth'],
                        ['3', 'QoS and traffic shaping', 'Simulation', 'Token bucket vs leaky bucket on the same workload: delay, throughput, queue length, drops'],
                        ['4', 'TCP vs UDP', 'Mixed', 'Real loopback sockets on this machine, or a separately labelled simulation of a lossy path'],
                        ['5', 'Detect, reroute, transfer', 'Simulation', 'Hysteresis detector → Dijkstra recalculation → simulated transfer, before and after a failure'],
                    ],
                },
            },
            {
                id: 'purpose',
                heading: 'Purpose',
                body: 'Make link-state route selection, congestion control, traffic shaping and transport trade-offs inspectable, and show how a failure detected at one layer changes behaviour at another.',
            },
            {
                id: 'specification',
                heading: 'Specification',
                bullets: [
                    'Dijkstra: costs are unitless and greater than 0; ties go to the lowest node name; a disabled link is ignored as if absent. The routing table is derived from the result — Dijkstra is an algorithm, not a routing protocol, and no real routing table is changed',
                    'TCP: Reno (RFC 5681) with the RFC 6298 retransmission timer; one flow, one drop-tail bottleneck; no other variant is mixed in',
                    'Shaping: fixed packet size, byte-metered buckets, same tail-drop queue limit for both shapers, seeded workloads',
                    'TCP vs UDP: real mode times sockets on 127.0.0.1 only and injects no loss or delay; simulated mode models them',
                    'Integration: heartbeats are a generated sequence, not live probes; a route is a path through a graph, not a physically independent network path',
                ],
                videoId: 'u0X5I19Kw40',
                videoTitle: 'Network Experiment Lab',
            },
            {
                id: 'limits',
                heading: 'Limitations',
                body: 'The models are educational and do not predict real-network performance. Distance-vector routing is not implemented. The multipath file-transfer engine (Feature C) and the live recovery overlay (Feature A) are separate systems and are not run inside the integration experiment.',
            },
        ],
        researchAnchor: [
            'E. W. Dijkstra, "A note on two problems in connexion with graphs", Numerische Mathematik 1, 1959',
            'J. Y. Yen, "Finding the k shortest loopless paths in a network", Management Science 17, 1971',
            'RFC 5681 — TCP Congestion Control: https://www.rfc-editor.org/rfc/rfc5681',
            'RFC 6298 — Computing TCP\'s Retransmission Timer: https://www.rfc-editor.org/rfc/rfc6298',
            'RFC 9293 — Transmission Control Protocol: https://www.rfc-editor.org/rfc/rfc9293',
            'RFC 768 — User Datagram Protocol: https://www.rfc-editor.org/rfc/rfc768',
            'A. S. Tanenbaum & D. J. Wetherall, Computer Networks — traffic shaping (leaky and token bucket)',
        ],
    },
];

export const getFeature = (id: string): Feature | undefined =>
    features.find(f => f.id === id);

export const getAdjacent = (id: string) => {
    const i = features.findIndex(f => f.id === id);
    return {
        prev: i > 0 ? features[i - 1] : null,
        next: i >= 0 && i < features.length - 1 ? features[i + 1] : null,
    };
};
