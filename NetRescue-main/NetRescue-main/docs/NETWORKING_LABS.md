# Network Experiment Lab

One guided journey of five experiments, added beside the original three
features (hysteresis recovery, multipath transfer, diagnostics) without changing
them. On the dashboard it is a single feature, **Network Experiment Lab**
(`/features/feature-e`); its five experiments are steps of a journey with a
*Next* button, not five separate entries in the features listing.

| Step | Experiment | URL (still directly reachable) | Execution mode | Backend |
| --- | --- | --- | --- | --- |
| 1 | Dijkstra shortest-path routing | `/features/routing/playground` | SIMULATION | `netrescue/labs/routing.py` |
| 2 | TCP congestion control | `/features/tcp-congestion/playground` | SIMULATION | `netrescue/labs/tcp_cc.py` |
| 3 | QoS / traffic shaping | `/features/traffic-shaping/playground` | SIMULATION | `netrescue/labs/shaping.py` |
| 4 | TCP vs UDP | `/features/tcp-udp/playground` | **MIXED** (real loopback sockets *or* simulation) | `netrescue/labs/tcp_udp.py` |
| 5 | Detect → reroute → transfer | `/features/integration/playground` | SIMULATION (drives the existing hysteresis class) | `netrescue/labs/integration.py` |

Simulation logic lives in `netrescue/labs/`, HTTP handling in
`apps/api/routers/labs.py` (prefix `/api/labs`), presentation in
`apps/dashboard/src/pages/*LabPage.tsx` with shared pieces in
`apps/dashboard/src/labs/`. The browser never computes a route, a congestion
window or a shaper schedule: every plotted value is an API response.

## Run it

```bash
pip install -r requirements.txt -r requirements-dev.txt
uvicorn apps.api.main:app --host 127.0.0.1 --port 8000      # terminal 1
cd apps/dashboard && npm install && npm run dev              # terminal 2  → http://127.0.0.1:3000
```

Open **Features → Network Experiment Lab → Start the guided journey**. API
documentation: <http://127.0.0.1:8000/docs>.

```bash
pytest tests/test_labs.py tests/test_routing_tables.py tests/test_execution_modes.py   # the labs
pytest                                                # everything (≈ 11 min)
cd apps/dashboard && npm test                         # frontend unit/component tests (Vitest)
cd apps/dashboard && npm run build                    # type-check + production build
cd apps/dashboard && npm run e2e                      # real Chrome against the running API + dev server
```

`npm run e2e` needs the API on :8000, the dev server on :3000 and a local
Chrome; it drives the installed browser through `playwright-core` (nothing is
downloaded) and compares what the page shows with independent API calls.

POST routes need the `operator` role (`X-NetRescue-Role: operator`); the
dashboard sends it. A read-only caller receives 403.

## Every feature explains how to run it

Each feature page and each experiment page opens with a compact five-step
**How to run this experiment** strip (set up → configure → act → inspect →
change and compare), specific to that experiment, *before* any purpose or concept
text. The wording lives in `apps/dashboard/src/labs/workflows.ts`; the detail
pages for features A, C, D and E show it above their *Purpose* section, and the
three original playgrounds (hysteresis, multipath, diagnostics) show it under
their headers.

## REAL versus SIMULATION — established by the backend

Two labels are used everywhere: **REAL NETWORK OPERATION** (short: `REAL`) and
**SIMULATION** (short: `SIMULATED`); a feature that contains both is **MIXED**.

* **REAL** — the operation is performed against the operating system or a real
  socket on this machine (a subprocess such as `ping`/`tracert`, the OS
  resolver, an OS adapter query, or a TCP/UDP socket). Loopback sockets are real
  sockets, but they do not cross a physical network; that is stated in the
  limitations of every loopback operation.
* **SIMULATION** — the result is produced by a model or algorithm; no
  corresponding live network operation occurs.

**How the mode is decided.** `netrescue/labs/execution.py` registers every
operation with its implementing code and a machine-checked claim: a REAL
operation's source must contain an OS/network primitive (`subprocess.run(`,
`socket.getaddrinfo(`, `socket.socket(`, `asyncio.start_server`, …); a
SIMULATION operation's source must contain none. `verify()` reads the source,
and an operation whose claim fails is reported as `UNVERIFIED` rather than
trusted. `tests/test_execution_modes.py` enforces every entry and proves a false
claim is caught. A caller cannot set the mode: request fields named `mode` or
`execution` are ignored.

* `GET /api/labs/execution` — every feature with its derived mode and verified
  operations (the dashboard's execution-mode panel reads this).
* Every result carries an `execution` block: `mode`, `operation` executed,
  `target` (for real operations), `model`/algorithm, `seed`, `inputs`, `outcome`,
  the verification `evidence`, `implementation`, and `limitations`. This is now
  attached to the lab endpoints and to the diagnostics, playground and transfer
  endpoints (added keys only; no existing field changed).

### Classification of every feature

| Feature | Mode | Why (verified in code) |
| --- | --- | --- |
| A · Hysteresis overlay (apply, fail, test) | REAL (loopback) | `netrescue.forwarding.node` opens `asyncio.start_server` listeners on 127.0.0.1 and forwards real packets; delivery is the destination's acknowledgement |
| A · Hysteresis vs naive replay | SIMULATION | replays a supplied heartbeat sequence; no sockets |
| C · Multipath transfer | SIMULATION | file bytes and SHA-256 are real, but paths are modelled delay/loss profiles inside one process (`netrescue.transfer.session`) — not sockets |
| C · Scheduler comparison | SIMULATION | queueing model |
| D · Ping, route trace, adapters, ping benchmark | REAL | OS `ping`/`tracert`/adapter queries through `subprocess.run` |
| D · DNS | REAL | OS resolver `socket.getaddrinfo` |
| D · Fault scenarios, hysteresis sweep, scheduler comparison | SIMULATION | seeded generators; no probes |
| E1 · Dijkstra, E2 · TCP, E3 · Shaping, E5 · Integration | SIMULATION | pure computation |
| E4 · TCP vs UDP | MIXED | *real loopback* mode opens sockets; *simulation* mode does not — separate tabs and results |

## Step 1 — Dijkstra, iteration table and routing table

**Terminology.** Dijkstra's algorithm computes least-cost paths from one source.
A *routing table* is derived from its result: per destination, the cost and the
first node after the source (the *next hop*). Dijkstra is an algorithm, not a
routing protocol — link-state protocols such as OSPF run it on a link-state
database; distance-vector protocols such as RIP use Bellman-Ford instead.
**Distance-vector routing is not implemented in this build**, and this lab
changes no real routing table.

**Cost** is a unitless administrative metric (like an OSPF interface cost): the
smallest sum wins. It is not derived from delay or loss. Costs must be finite and
> 0; links are bidirectional with one cost; a disabled link is ignored as if
absent. **Tie-breaking:** equal tentative distances are resolved by the lowest
node name, so results are deterministic.

**Iteration table.** The algorithm records a snapshot at initialisation
(iteration 0) and after each iteration: selected node, visited (finalised) set,
tentative distance and predecessor for every node (`null`/`∞` = no path yet, never
shown as 0), and the relaxations performed. Snapshots are captured *while the
algorithm runs* (`netrescue/labs/routing.py`, `Iteration`), not rebuilt from the
answer. The page lets you step forward and backward; changed cells are
highlighted and the topology highlights the selected node, visited nodes,
relaxed links and the shortest-path tree for the iteration being viewed.

**Final routing table.** One row per node — *Destination, Shortest cost, Next
hop, Complete path, Status* (`Source`, `Reachable`, `Unreachable`). The source is
handled explicitly (next hop "local"); unreachable destinations show `∞` and no
next hop. It follows the source, weights and link availability, and is shown once
the final iteration is reached.

**Dynamic behaviour.** Edits (weights, link enable/disable, nodes, links, source,
destination) recalculate automatically (toggle off to run on demand). Results are
flagged *out of date* when inputs change and a late response for older inputs is
discarded. Obvious input errors (non-positive cost, missing endpoints) are
explained before any request is sent; the API validates again (422).

## Step 2 — TCP congestion control

Variant: **TCP Reno** (RFC 5681 congestion control, RFC 6298 retransmission
timer); nothing from CUBIC, BBR, NewReno or SACK is mixed in. Reno leaves fast
recovery on the first new ACK (including a partial ACK), so several losses in one
window often end in a timeout — real Reno behaviour, visible in the output.
Assumptions: one flow; one FIFO drop-tail bottleneck; seeded independent random
loss; `rtt_ms` is base propagation RTT with queueing and serialisation added;
ACKs are immediate, cumulative and never lost; unlimited receiver window; no
delayed ACK, SACK, timestamps or ECN; headers ignored; the optional handshake is
loss-free and costs one RTT. A model, not a kernel experiment. Only
`variant: "reno"` is accepted.

## Step 3 — traffic shaping

Token bucket and leaky bucket over **the same seeded arrival list**. Fixed packet
size; bucket metered in bytes; the token bucket starts full and releases a packet
instantly once enough tokens exist (infinitely fast output link), so bursts up to
the bucket depth pass; the leaky bucket releases one packet at a time at exactly
the configured rate. Both use a tail-drop FIFO ("buffered" = arrived and not yet
departed). Throughput = delivered bits ÷ max(duration, last departure). Patterns:
steady, burst, overload, Poisson (seeded). Oversized workloads (> 20,000 packets)
are rejected *before* they are generated.

## Step 4 — TCP vs UDP

**Real loopback mode** connects TCP and UDP sockets on `127.0.0.1`, both ends in
the API process (threads). It measures TCP `connect()` time, request/response
latency (p50/p95), TCP bulk throughput, UDP send rate, and UDP datagrams received,
reordered and duplicated. Loopback has no latency, bandwidth cap or normal loss, so
the result describes this machine's stack, **not a network**, and no loss or delay
is injected (that would need a kernel facility such as tc/netem or WinDivert,
which this project deliberately does not use). The real tab has no loss or RTT
controls because they would have no effect. One run at a time (409 if busy).

**Simulation mode** pits the Reno model against a paced UDP sender over one
modelled bottleneck, one flow at a time. Findings are generated from the run's own
numbers; the page never claims one protocol is generally faster or better. The two
modes keep separate results; switching tabs never shows one mode's result as the
other's.

## Step 5 — detect → reroute → transfer

`POST /api/labs/integration/run`:

1. A heartbeat sequence (generated outage, or supplied) drives the **existing**
   `netrescue.recovery.state_machine.HysteresisStateMachine` — same class and
   thresholds (`f_thresh`, `n`, `r_thresh`, optional EWMA) as the recovery lab.
2. SUSPECT multiplies the monitored link's cost; FAILED marks it down; HEALTHY
   restores it. Dijkstra re-runs after every state change.
3. Each route becomes RTT = 2 × Σ delay, loss = 1 − Π(1 − loss), bandwidth =
   min over links; the same TCP transfer is simulated over **baseline**,
   **degraded-but-not-rerouted**, and **after reroute**.
4. The response includes the timeline, per-phase metrics, outcome notes and a
   CSV; the page exports JSON and CSV.

| Boundary | Statement |
| --- | --- |
| Heartbeats | A supplied/generated sequence — not live probes. The live overlay is Feature A. |
| Routing | A graph computation; it does not change any operating-system route. |
| Transport | Reduces a route to RTT, loss and bottleneck bandwidth, then runs the TCP model. |
| Shaping | Independent of TCP; not part of the integrated experiment. |
| Multipath transfer | The existing engine is **not** run here, and a "route" is a graph path, not a physically independent network path. |

## Safety

No lab runs a command from user input, changes an interface, route or firewall
rule, or listens on anything but `127.0.0.1` (the real TCP/UDP experiment binds
ephemeral loopback ports for one request). All inputs are range-validated by the
API schema and by the domain module; limits cap event counts, packets, durations
and transfer sizes.

## Known limitations

* The models are educational and do not predict real-network performance.
* TCP: one variant, one flow, one bottleneck. Shaping: one traffic class.
* Real TCP/UDP: single run per call, loopback only, shared process and GIL.
* The integration uses a generated heartbeat sequence; wiring the live overlay's
  detector into it is future work. Distance-vector routing is not implemented.
* The `execution` verification reads source text for primitives; it proves the
  implementation *contains* (or lacks) OS/network calls, not that every code path
  reaches them. Real-operation outcomes (e.g. an unreachable ping target) are
  reported as measured.
* The dashboard bundle is over Vite's 500 kB advisory size (no code splitting was
  added).
