# Feature 3 — Network Diagnostics & Benchmarking Lab

This document replaces the previous **Feature D — Network Emulation** (`tc`/`netem`)
description. Feature 3 is now a Windows-native diagnostics and benchmarking lab:
it measures this machine's network for real, runs repeatable benchmarks, and
reproduces fault behaviour with a deterministic simulator.

The previous Linux-only emulator is **not** used by Feature 3. It is retained,
unchanged and capability-detected, as a legacy surface (see §2 and §18).

> **Illustrative output.** Every JSON and console sample below is marked
> *illustrative*. It shows the *shape* of a response, not a measurement of any
> particular machine. Numbers in real responses come from the host the API runs
> on and will differ.

---

## 1. Purpose

Feature 3 answers three questions without needing Linux, WSL2, kernel modules or
administrator rights:

1. **What is this machine's network doing right now?** Real latency, packet loss,
   route, DNS resolution time, adapter link state and IP configuration.
2. **What happens under controlled conditions?** Repeatable benchmarks, a seeded
   fault simulator, a hysteresis sweep and a scheduler comparison.
3. **What does the rest of NetRescue measure against?** A real baseline that can
   bracket a Feature A (hysteresis recovery) experiment, and a diagnostics run
   that can be paired with a Feature B (multipath transfer) summary.

The design rule throughout: **never present a modelled number as a measured
one.** Every result carries a provenance label (`real`, `controlled`,
`simulated` or `unsupported`), a configuration hash and the software version.

## 2. Scope and non-goals

**In scope**

- Real, unprivileged Windows probes: ICMP latency/loss, route trace, DNS, adapter
  and IP configuration.
- A benchmark runner with history, comparison and JSON/CSV export.
- A deterministic, seeded fault simulator (unreachable, jitter, loss, timeout,
  intermittent, healthy↔unhealthy transitions).
- Harnesses that reuse the existing `netrescue.benchmark` sweep and comparison.
- Integration points for Feature A and Feature B.
- A connected dashboard with eight sections and a one-click **Run Diagnostics**
  button.

**Non-goals (and why)**

- **Kernel-level packet shaping.** `tc`/`netem` is a Linux kernel facility
  (see tc-netem(8), §19). It cannot run on Windows 10/11 without WSL2 or a Linux
  VM. Feature 3 does not use it and does not pretend to. The legacy
  `netrescue.emulation` module remains available for Linux hosts and reports
  itself unavailable elsewhere.
- **No interface, route or firewall is modified.** No privileged action is
  required or taken. The lab observes; it does not reconfigure the host.
- **No single combined score.** Latency, loss, jitter and duration are different
  quantities. Collapsing them into one index would hide the trade-offs the
  feature exists to expose, so it is deliberately not done.

## 3. Platform requirements

- **Windows 10 or 11** for the real measurements (`ping`, `tracert`,
  `Get-NetAdapter` and related PowerShell cmdlets).
- **No administrator rights** for basic diagnostics.
- **Python 3.11+** for the API. Node 18+ for the dashboard.
- **A database is optional.** Run history falls back to an in-process store when
  PostgreSQL is unreachable, and says so.

### Running on Windows

```powershell
# from the repository root
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

# 1. API (must be up before the dashboard is useful)
uvicorn apps.api.main:app --host 127.0.0.1 --port 8000

# 2. Dashboard
cd apps/dashboard
npm install
npm run dev          # http://127.0.0.1:5173

# 3. Open the feature and click "Run Diagnostics"
#    http://127.0.0.1:5173/features/feature-d
```

The dashboard reads `VITE_API_BASE_URL` (default `http://127.0.0.1:8000`) and
sends `X-NetRescue-Principal` / `X-NetRescue-Role` headers. The diagnostics lab
asks for the `operator` role; read-only probes accept `readonly`.

## 4. Architecture

```
Dashboard  (React + TypeScript + Vite)
   │  HTTP REST
   ▼
FastAPI  apps/api/routers/diagnostics.py   (prefix /api/diagnostics)
   │
   ├── netrescue/diagnostics/probes.py    real OS commands + parsers
   ├── netrescue/diagnostics/runner.py    repeatable real benchmarks
   ├── netrescue/diagnostics/faults.py    seeded fault simulator
   ├── netrescue/diagnostics/service.py   the composed "Run Diagnostics" bundle
   ├── netrescue/diagnostics/harness.py   sweep, comparison, Feature A/B links
   ├── netrescue/diagnostics/report.py    exports, comparison, measurement report
   ├── netrescue/diagnostics/store.py     bounded in-process history (+ best-effort DB)
   └── netrescue/diagnostics/config.py    normalisation + SHA-256 config hash
   │
   ├── netrescue/benchmark/sweep.py        reused: hysteresis sweep (Feature F)
   └── netrescue/benchmark/comparison.py   reused: scheduler comparison (Feature G)
```

Real subprocess calls are executed off the event loop (`asyncio.to_thread`), so a
slow `tracert` never blocks another request. The router depends on
`netrescue.core` only for optional persistence; the labs themselves have no
database dependency.

## 5. Backend modules

| Module | Responsibility |
| --- | --- |
| `config.py` | Normalise a configuration mapping, canonicalise it to JSON, hash it with SHA-256. Defines `SOFTWARE_VERSION`. |
| `probes.py` | Validate targets; run `ping`, `tracert`, DNS; enumerate adapters and IP configuration; parse output into structured results. Never raises for "unavailable" — returns `available: false` with a reason. |
| `runner.py` | `BenchmarkSpec` → `BenchmarkRun`. Runs *n* repetitions of *k* probes, computes aggregate statistics, `to_json()` / `to_csv()`. Also hosts the shared simulation used by `faults.py`. |
| `faults.py` | `FaultScenario` → `FaultScenarioResult`. A seeded model of unreachable / jitter / loss / timeout / intermittent / healthy→unhealthy, with a healthy/unhealthy timeline and transitions. |
| `service.py` | `run_full_diagnostics()` — the single real operation behind the button. Each section (ping, adapters/IP, route, DNS) is independent and reports its own status. |
| `harness.py` | Wraps the existing hysteresis sweep and scheduler comparison; captures Feature A baselines; pairs Feature B transfers. |
| `report.py` | JSON/CSV export, multi-run comparison (no combined score), and the per-run measurement report. |
| `store.py` | Thread-safe, bounded, in-process run history, plus best-effort persistence into the existing `experiments` / `metric_samples` tables. |

## 6. API reference

All routes are under `/api/diagnostics`. `READ` routes accept a `readonly`
principal; `WRITE` routes require `operator`.

| Method | Path | Permission | Purpose |
| --- | --- | --- | --- |
| GET | `/status` | READ | Capability report: real / controlled / unsupported |
| GET | `/scenarios` | READ | The simulated fault scenarios and their knobs |
| GET | `/network` | READ | Adapters, IP configuration, default gateway |
| POST | `/ping` | READ | Real ICMP latency/loss statistics |
| POST | `/traceroute` | READ | Real route trace |
| POST | `/dns` | READ | Real DNS resolution + duration |
| POST | `/run` | WRITE | The composed bundle behind the button |
| POST | `/benchmarks` | WRITE | Repeatable real benchmark |
| POST | `/faults` | WRITE | Deterministic simulated fault scenario |
| POST | `/hysteresis-sweep` | WRITE | Ratio sweep + trade-off curve |
| POST | `/scheduler-comparison` | WRITE | Scheduler variant comparison |
| GET | `/runs` | READ | Stored run history (summaries) |
| GET | `/runs/{id}` | READ | One stored run |
| GET | `/runs/{id}/report` | READ | Measurement report |
| GET | `/runs/{id}/export?format=json\|csv` | READ | Export one run |
| POST | `/runs/compare` | READ | Side-by-side comparison |
| DELETE | `/runs` | WRITE | Clear the in-process history |
| POST | `/integrations/feature-a/capture` | WRITE | Real baseline (`before`/`after`) |
| POST | `/integrations/feature-a/compare` | READ | Compare the baseline pair |
| POST | `/integrations/feature-b` | READ | Pair a transfer summary with a run |
| GET | `/integrations/feature-b/{run_id}` | READ | Fetch a stored pairing |

**Status codes.** Invalid input → `422`. Command missing or unsupported → `503`.
Timeout → `504`. Command failed → `502`. A ping with **zero replies is a valid
`200`** whose `reachable` field is `false`; a target that cannot be reached is a
result, not a server error.

## 7. Real diagnostics

### 7.1 Ping

`POST /api/diagnostics/ping` runs the operating system's `ping` with an argument
array (`["ping", "-n", "4", "-w", "2000", target]`) and `shell=False`. It parses
sent/received/lost, min/avg/max/stddev and the individual per-probe samples.

Sub-millisecond replies (Windows reports `time<1ms`) are recorded as `0.0` and
counted in `sub_millisecond_samples`; the average is therefore described as a
**lower bound**, not a precise value.

*Illustrative response:*

```json
{
  "target": "127.0.0.1",
  "sent": 4,
  "received": 4,
  "loss_rate": 0.0,
  "loss_pct": "0.0%",
  "samples_ms": [0.0, 0.0, 3.0, 1.0],
  "min_ms": 0.0,
  "avg_ms": 1.0,
  "max_ms": 3.0,
  "stddev_ms": 1.22,
  "sub_millisecond_samples": 2,
  "units": "ms",
  "reachable": true,
  "notes": [
    "ICMP is frequently rate-limited, deprioritised or blocked outright ...",
    "A reply measures this host's ICMP round-trip time, not application latency ...",
    "2 reply(ies) were reported as '<1ms' and recorded as 0 ms, so the average is a lower bound ..."
  ]
}
```

### 7.2 Route trace

`POST /api/diagnostics/traceroute` runs `tracert -d -h <max_hops> -w <ms>
<target>` and parses each hop, its round-trip times and whether it timed out. A
silent hop (`* * * Request timed out.`) is reported as `timed_out: true` with
`rtt_ms: null` — it is **not** interpreted as a failed forwarder, because routers
commonly deprioritise or filter the probe.

### 7.3 DNS

`POST /api/diagnostics/dns` resolves a name through the operating system
resolver and reports the addresses plus how long resolution took, in
milliseconds. Resolution time includes cache hits, so a repeat lookup is usually
faster; the response says so.

### 7.4 Adapters and IP configuration

`GET /api/diagnostics/network` returns, from PowerShell:

- `Get-NetAdapter` → `{name, description, status, link_speed, mac_address, if_index}`.
- `Get-NetIPAddress` + `Get-NetRoute` + `Get-DnsClientServerAddress` → per-interface
  IPv4 addresses, default gateway and DNS servers.

The cmdlets are projected to plain objects *inside* PowerShell
(`[pscustomobject]@{}`) before `ConvertTo-Json`. Selecting properties off a CIM
instance serialises the whole WMI class graph and makes the query time out;
projecting first keeps it to a few seconds. Adapter/IP results are cached for
15 seconds (reported as `cached` / `cache_ttl_s`) so the dashboard stays
responsive; the cache is short enough that a real change still appears.

## 8. Benchmark runner

`POST /api/diagnostics/benchmarks` runs `repetitions` rounds of `count` probes
against a target and stores the structured result. Each repetition is a
`RepetitionOutcome`; the run has an `aggregate` with sent/received/lost, loss
rate, and min/avg/max/stddev/median latency.

*Illustrative aggregate:*

```json
{
  "sent": 12, "received": 12, "lost": 0,
  "packet_loss_rate": 0.0, "packet_loss_pct": "0.00%",
  "min_ms": 0.0, "avg_ms": 1.4, "max_ms": 9.0,
  "stddev_ms": 1.9, "median_ms": 1.0, "samples": 12
}
```

The benchmark is **repeatable, not reproducible**: the same configuration and
seed produce the same `config_hash`, but a real network legitimately varies
between runs, and the report never claims otherwise.

## 9. Simulated fault scenarios

`POST /api/diagnostics/faults` runs a **seeded, deterministic** model. A fixed
seed always produces an identical `config_hash`, an identical probe timeline and
identical transitions. No network call is made.

| Scenario | Models |
| --- | --- |
| `unreachable` | Every probe fails (black hole / host down) |
| `latency_jitter` | Replies arrive, slowly and inconsistently |
| `packet_loss` | A steady fraction of probes is dropped |
| `timeout` | Modelled latency exceeds the probe timeout |
| `intermittent` | Alternating reachable/unreachable windows |
| `healthy_to_unhealthy` | Healthy, then degraded, then recovered |
| `transitions` | Healthy/unhealthy transitions for the state view |

The response includes a per-probe `timeline` (`ok`, `latency_ms`, `state`), the
`transitions` between HEALTHY and UNHEALTHY, counts, and the shared simulated
benchmark aggregate. Everything is marked `"simulated": true` and
`"data_kind": "simulated"`, and `limitations` states that the seed makes the run
reproducible but cannot make a real network deterministic.

## 10. Feature A integration

Feature A is the hysteresis recovery lab. Feature 3 adds a real measurement that
can bracket an experiment:

- `POST /integrations/feature-a/capture` with `phase: "before" | "after"` runs a
  real ping benchmark and annotates it. The annotation states that Feature A's
  own link states come from its in-process simulator and are a different kind of
  evidence — a healthy real baseline does **not** validate the simulator.
- `POST /integrations/feature-a/compare` computes the arithmetic delta between
  the two real measurements and reports `same_configuration`. Its
  `interpretation` is explicit that the delta alone does not establish that the
  Feature A experiment caused the change, because the network also moves on its
  own.

## 11. Feature B integration

Feature B is the multipath transfer lab. `POST /integrations/feature-b` accepts a
transfer summary plus a stored diagnostics run id and returns two clearly
separated columns:

- **Diagnostics** — `data_kind: "real"`, this machine's ICMP measurement.
- **Transfer** — `data_kind: "controlled"`; scheduler variant, retransmission
  policy, chunk counts, goodput, retransmission/out-of-order counts, integrity.
  The notes state that Feature B's paths are **logical**, not independent
  physical interfaces, and are not measured as separate real sockets.

If the diagnostics run is not stored, the endpoint returns `404` rather than
fabricating a successful association. The transfer summary is not written into
the diagnostics run's own record — the two remain separate objects shown beside
each other.

## 12. Hysteresis sweep harness (§Feature F)

`POST /api/diagnostics/hysteresis-sweep` reuses `netrescue.benchmark.sweep`. One
identical seeded heartbeat-loss sequence is replayed for every hysteresis ratio,
so the dead band is the **only** variable; any difference in flap count is
attributable to it. Ratio `1.0` is always included as the no-dead-band reference,
and the naive single-threshold baseline is reported alongside. The result is
labelled `data_kind: "controlled"`.

The response carries `points[]` (per ratio: flaps, recovery seconds, detection
seconds), `naive`, and a `pareto_frontier` of non-dominated points. There is no
combined score; the trade-off is the two axes.

*Illustrative points:*

```
ratio  flaps  recovery_s  detection_s
1.0    12     1.0         4.0
1.5    7      2.0         6.0
2.0    3      3.0         9.0
2.5    3      4.0         11.0
3.0    3      5.0         14.0
```

## 13. Scheduler comparison harness (§Feature G)

`POST /api/diagnostics/scheduler-comparison` reuses
`netrescue.benchmark.comparison`. Every scheduler variant (DB-DSP, RR, WRR,
SINGLE, NAIVE_DELAY) and retransmission policy (RTX-CL, RTX-CWND) is rebuilt from
the same declarative `PathSpec` list, and each cell is repeated at least five
times. Unknown variant names are refused with `422` and the list of valid names.

The result is `data_kind: "controlled"` and its provenance states that the paths
are logical, not independent physical interfaces. This harness measures the
scheduling policy, not the wire.

## 14. Configuration integrity

Every run normalises its configuration, canonicalises it (sorted keys, stable
float formatting) and hashes it with SHA-256. The hash, the software version and
(when applicable) the seed travel with the result and appear in exports and
reports. Two runs with the same hash were set up identically; a comparison says
so when it notices that. Canonicalisation makes the hash independent of key order
but sensitive to value changes and to list order (which matters — ratio order and
path order are part of the configuration).

## 15. Frontend dashboard

The Feature 3 page is `/features/feature-d`. A white **Run Diagnostics** button
sits at the right of the hero header (white background, dark text, subtle border,
the same radius and typography as the other feature buttons, with a loading
state). It performs one real operation and renders the result inline; errors are
shown verbatim.

Below it, `DiagnosticsLab` renders eight sections, each with a provenance badge:

1. **Live Network Status** — gateway, adapters up, host, platform, and a live DNS lookup.
2. **Latency & Packet Loss** — real ping statistics, a per-probe bar chart, a
   benchmark control, and a simulated fault-timeline control.
3. **Route Trace** — the real hop table with silent hops marked.
4. **Network Adapters** — adapter and IPv4 configuration tables.
5. **Benchmark History** — stored runs, multi-select comparison, per-run export.
6. **Hysteresis Sweep** — ratio table, trade-off chart and Pareto highlight.
7. **Scheduler Comparison** — variant table with integrity counts.
8. **Export Reports** — JSON/CSV download of a run plus the capability report.

Every section has loading, empty, success, partial and error states; inputs are
validated and labelled; charts carry `role="img"` and an accessible description.

## 16. Provenance and honesty rules

- **Real** is used only for a measurement taken on this host just now.
- **Controlled** is used for a benchmark over modelled, declarative conditions
  (sweep, scheduler comparison, Feature B paths).
- **Simulated** is used for the seeded fault simulator.
- **Unsupported** is used for a capability this host cannot provide, with the
  reason and, where relevant, a reference.
- A **partial** run is reported as `PARTIAL`: a failed route trace does not
  invalidate the ping statistics, and the bundle lists which sections succeeded.
- No metric is fabricated. If a section cannot run, it says so.
- No single combined score is produced anywhere.

## 17. Testing

`tests/test_diagnostics.py` contains 54 tests in two groups:

- **Pure unit tests** — config hashing, target validation (injection rejection),
  the ping and tracert parsers, and the seeded fault simulator's determinism,
  divergence, error handling and labelling.
- **HTTP tests** over the real FastAPI app using loopback targets and short
  timeouts. The database dependency is overridden to `None` so a run is never
  written to a developer's configured PostgreSQL.

```powershell
pytest tests/test_diagnostics.py -v
pytest tests/test_diagnostics.py -k "Fault or Parse or Config" -v
```

The whole suite (baseline + diagnostics) is run with plain `pytest`.

## 18. Limitations and unsupported capabilities

- **Kernel-level emulation is unavailable here.** `tc`/`netem` is a Linux kernel
  facility. Feature 3 does not use it. The legacy `netrescue.emulation` package
  and its `/api/emulation` router are retained, capability-detected, for Linux
  hosts; on Windows they report themselves unavailable with a reason. Nothing in
  Node 3 depends on them.
- **No interface, route or firewall is modified**, and no privileged action is
  taken. This is a measurement tool, not a traffic shaper.
- **ICMP is not application latency.** Routers and targets frequently
  rate-limit, deprioritise or block ICMP; a missing reply is not proof of a
  fault. Every ping/trace result repeats this caveat.
- **Run history is in-process.** It is bounded (200 runs) and is cleared when the
  API restarts. When a database is available, runs are also written best-effort
  into the existing `experiments` / `metric_samples` tables; the `persisted`
  flag reports whether that happened.
- **Benchmarks are repeatable, not reproducible.** A real network varies between
  runs even with an identical configuration.
- **Sub-millisecond replies are recorded as `0.0`** and counted, so averages are
  lower bounds on very fast paths.
- **The simulator is a model.** A seed makes it reproducible; it cannot make a
  real network deterministic, and it is never presented as a measurement.
- **The legacy `apps/console` app** still refers to `tc`/`netem` in its
  security/benchmark screens. Those screens read the legacy capability endpoint
  and are out of scope for this change.

## 19. Research references

These are canonical documentation pages and published works. No citation below is
invented; anything that could not be verified is described in prose rather than
linked.

**Windows commands and cmdlets**

- Microsoft Learn — `ping` command:
  <https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/ping>
- Microsoft Learn — `tracert` command:
  <https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/tracert>
- Microsoft Learn — `Get-NetAdapter`:
  <https://learn.microsoft.com/en-us/powershell/module/netadapter/get-netadapter>
- Microsoft Learn — `Get-NetIPAddress`:
  <https://learn.microsoft.com/en-us/powershell/module/nettcpip/get-netipaddress>
- Microsoft Learn — `Get-NetRoute`:
  <https://learn.microsoft.com/en-us/powershell/module/nettcpip/get-netroute>
- Microsoft Learn — `Get-NetIPConfiguration`:
  <https://learn.microsoft.com/en-us/powershell/module/nettcpip/get-netipconfiguration>
- Microsoft Learn — `Get-DnsClientServerAddress`:
  <https://learn.microsoft.com/en-us/powershell/module/dnsclient/get-dnsclientserveraddress>

**Protocol**

- RFC 792 — Internet Control Message Protocol (ICMP):
  <https://www.rfc-editor.org/rfc/rfc792>

**The replaced facility**

- `tc-netem(8)`, Linux manual page:
  <https://man7.org/linux/man-pages/man8/tc-netem.8.html>

**Research the apparatus serves**

- Maltz thesis (Carnegie Mellon University), §7.6.1 — route hysteresis for
  ad-hoc/overlay networks (Feature A).
- Verma et al., *Journal of Network and Systems Management*, 2022 — DB-CMT /
  DB-DSP / RTX-CL delay-based multipath scheduling (Feature B).
