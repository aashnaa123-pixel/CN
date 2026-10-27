

<h1 align="center">🌌 NetRescue</h1>

<p align="center">
  <strong>Hysteresis-Based Recovery, Multipath File Transfer & Network Diagnostics</strong>
</p>

<p align="center">
  <em>Detect unstable links · Transfer files across logical paths · Measure and analyze network behavior</em>
</p>

<p align="center">
  <a href="#-architecture"><img src="https://img.shields.io/badge/Network_Recovery-Hysteresis-9F1239?style=for-the-badge" alt="Network Recovery" /></a>
  <a href="#-features"><img src="https://img.shields.io/badge/Transfer-Multipath-7C3AED?style=for-the-badge" alt="Multipath Transfer" /></a>
  <a href="#-network-diagnostics"><img src="https://img.shields.io/badge/Diagnostics-Windows_Compatible-0891B2?style=for-the-badge" alt="Windows Diagnostics" /></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/React-61DAFB?style=flat-square&logo=react&logoColor=black" alt="React" />
  <img src="https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white" alt="TypeScript" />
  <img src="https://img.shields.io/badge/Vite-646CFF?style=flat-square&logo=vite&logoColor=white" alt="Vite" />
  <img src="https://img.shields.io/badge/Testing-pytest-0A9EDC?style=flat-square&logo=pytest&logoColor=white" alt="pytest" />
  <img src="https://img.shields.io/badge/Platform-Windows-0078D4?style=flat-square&logo=windows&logoColor=white" alt="Windows" />
  <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License — verify repository license" />
</p>

---

## 🔥 The Problem We're Solving

Network failures are not always clean or permanent. A forwarding process can stop responding, link quality can fluctuate, and file chunks travelling through paths with different delays can arrive out of order.

Simple failure thresholds can cause **route flapping**. A single transfer path can become a bottleneck, while network diagnostics alone may not explain how different failure conditions affect an application's behavior.

**NetRescue** brings together three networking experiments in one dashboard:

- 🛡️ **Hysteresis Recovery:** models health-state transitions and recovery behavior to reduce unnecessary switching.
- 📦 **Multipath Transfer:** divides files into chunks, schedules them across logical paths, reassembles the data and checks integrity.
- 🔬 **Network Diagnostics:** collects available Windows network measurements and compares controlled, deterministic simulations.


---

## ⚡ One-Glance Overview

```text
┌──────────────────────────────────────────────────────────────────────┐
│                              NETRESCUE                               │
│                                                                      │
│  🛡️ HYSTERESIS RECOVERY     📦 MULTIPATH TRANSFER   🔬 DIAGNOSTICS  │
│  ─────────────────────     ─────────────────────   ──────────────  │
│  Heartbeat monitoring      File chunking           Windows ping     │
│  HEALTHY / SUSPECT /       Logical path scheduling  Route tracing    │
│  FAILED states             Retransmission policies  DNS resolution   │
│  Dead-band experiments     Ordered reassembly       Adapter status   │
│  Recovery measurements     SHA-256 verification     IP configuration │
│                                                                      │
│  📊 EXPERIMENTS              📈 RESULTS               📄 REPORTS       │
│  ──────────────              ─────────               ─────────       │
│  Hysteresis sweeps           Latency and timing      CSV / JSON*      │
│  Scheduler comparisons       Completion and loss     Config hashes*  │
│  Seeded fault scenarios      Recovery and stability  Run metadata*   │
│                                                                      │
│   
└──────────────────────────────────────────────────────────────────────┘
```

---

## 🏗️ Architecture

<p align="center">
  <img src="https://img.shields.io/badge/Frontend-React_%2B_TypeScript-61DAFB?style=for-the-badge&logo=react&logoColor=black" alt="React and TypeScript" />
  <img src="https://img.shields.io/badge/Backend-FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Experiments-Real_%2B_Simulated-7C3AED?style=for-the-badge" alt="Real and simulated experiments" />
</p>

```text
                        ┌─────────────────────┐
                        │  React Dashboard    │
                        │  TypeScript + Vite  │
                        └──────────┬──────────┘
                                   │ HTTP / JSON
                        ┌──────────▼──────────┐
                        │    FastAPI Server   │
                        │  REST API + Routers │
                        └──────────┬──────────┘
                                   │
               ┌───────────────────┼───────────────────┐
               │                   │                   │
      ┌────────▼────────┐ ┌────────▼────────┐ ┌────────▼────────┐
      │ Hysteresis      │ │ Multipath       │ │ Network         │
      │ Recovery        │ │ Transfer        │ │ Diagnostics     │
      ├─────────────────┤ ├─────────────────┤ ├─────────────────┤
      │ Heartbeat state │ │ Chunk scheduler │ │ Ping / tracert  │
      │ State changes   │ │ Retransmission  │ │ DNS / adapters  │
      │ Recovery sweeps │ │ Reassembly      │ │ IP / route data │
      └────────┬────────┘ └────────┬────────┘ └────────┬────────┘
               │                   │                   │
               └───────────────────┼───────────────────┘
                                   │
                        ┌──────────▼──────────┐
                        │ Metrics and Results │
                        │ Charts / Run History│
                        │ Reports, if enabled │
                        └─────────────────────┘
```

### 🧰 Technology Map

| Technology | Purpose |
|:--|:--|
| **Python** | Implements networking logic, state transitions and experiment models. |
| **FastAPI** | Exposes backend operations through HTTP endpoints. |
| **React + TypeScript** | Builds the interactive networking dashboard. |
| **Vite** | Runs and builds the frontend. |
| **Asyncio** | Supports asynchronous application-level transfer and timing behavior. |
| **SHA-256** | Verifies that reconstructed file contents match the expected digest. |
| **pytest** | Tests backend behavior and regression cases. |
| **Windows networking tools** | Provide real measurements using tools such as `ping`, `tracert`, DNS resolution and PowerShell networking commands. |

---

## 🛡️ Feature 1 — Hysteresis-Based Recovery



### The problem

When network quality fluctuates around one threshold, a naïve detector may alternate repeatedly between healthy and failed states. This can cause unnecessary route changes and unstable recovery behavior.

### How NetRescue approaches it

- Tracks health using a state machine with `HEALTHY`, `SUSPECT` and `FAILED` states.
- Uses configurable heartbeat and failure/recovery parameters.
- Uses a dead band between failure and recovery conditions to reduce oscillation.
- Supports hysteresis-ratio experiments to compare stability and recovery behavior.
- Can record transition counts and recovery timing where supported by the implementation.

### State transition model

```text
                Missed heartbeats
       ┌─────────────────────────────────┐
       │                                 ▼
┌─────────────┐                   ┌─────────────┐
│   HEALTHY   │                   │   SUSPECT   │
└─────────────┘                   └──────┬──────┘
       ▲                                  │
       │                                  │ Failure threshold reached
       │                                  ▼
       │                           ┌─────────────┐
       │                           │   FAILED    │
       │                           └──────┬──────┘
       │                                  │
       └──────────────────────────────────┘
           Successful heartbeats reach
           the configured recovery threshold
```

### Why it matters

A failure detector should not treat every temporary quality drop as a permanent failure. Hysteresis gives the system separate conditions for entering and leaving a failure state.

**Important boundary:** State-machine recovery is not proof of a production overlay routing protocol. End-to-end forwarding recovery, process termination tests, route versioning and loop prevention should only be claimed when those behaviors have been implemented and tested.

---

## 📦 Feature 2 — Multipath Reliable File Transfer



### The problem

Different network paths can have different delays and bandwidths. File chunks can arrive out of order, and missing chunks may need retransmission before the original file can be reconstructed.

### How NetRescue approaches it

- Splits a file into smaller chunks.
- Assigns chunks to **logical paths** with declared delay, bandwidth and other supported conditions.
- Compares scheduling approaches such as DB-DSP, Round Robin, Weighted Round Robin and Single-Path.
- Supports the implemented retransmission policies, including RTX-CL and RTX-CWND.
- Reassembles received chunks using their offsets.
- Uses SHA-256 to compare the reconstructed file against the expected content.

### Transfer flow

```text
┌───────────────────┐
│   Source File     │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│  Split into       │
│  Numbered Chunks  │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│  Path Scheduler   │
└─────┬─────┬───────┘
      │     │
      ▼     ▼
   Path A  Path B  ... Path C
      │     │           │
      └─────┼───────────┘
            ▼
┌───────────────────┐
│ Reorder / Recover │
│ Missing Chunks    │
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ Reassemble File   │
│ Verify SHA-256    │
└───────────────────┘
```

### Metrics worth reporting

| Metric | What it tells us |
|:--|:--|
| Completion status | Whether the transfer completed. |
| Transfer duration | How long the application-level experiment took. |
| Out-of-order chunks | How often chunks arrived in a different order from their original sequence. |
| Retransmissions | How many chunks were retransmitted. |
| Integrity result | Whether the reconstructed file matches the expected SHA-256 digest. |
| Per-path utilization | How chunks were distributed among paths, if measured by the current implementation. |

### Why it matters

This feature demonstrates how chunk scheduling, retransmission, reassembly and integrity verification work together in a reliable file-transfer design.

**Important boundary:** The logical paths in the simulator are not necessarily separate physical interfaces or independently routed network connections. Results depend on the configured conditions. Multipath scheduling is not guaranteed to outperform Single-Path for every workload.

---

## 🔬 Feature 3 — Windows Network Diagnostics & Benchmarking



### The problem

Network experiments often require Linux-specific tools or privileged network configuration. On a Windows development machine, kernel-level `tc/netem` emulation is not available natively.

### What this feature provides

- Reads the available network adapter and IP configuration.
- Checks the configured default gateway and routing information.
- Measures real ICMP round-trip statistics using supported probes.
- Resolves hostnames through the operating system resolver.
- Runs bounded route tracing using Windows `tracert`.
- Compares controlled, seeded application-level fault scenarios.
- Runs hysteresis sweeps and scheduler comparisons under declared conditions where supported.
- Presents run history and exports only where the corresponding functionality is implemented.

### Provenance labels

| Label | Meaning |
|:--|:--|
| **Real** | Collected from the operating system or a live network probe. |
| **Controlled** | Compared under declared, repeatable experiment settings. |
| **Simulated** | Generated by the application-level fault model rather than measured from the physical network. |
| **Seeded** | Uses a configured random seed to make supported simulated experiments repeatable. |
| **Unavailable** | A capability is not provided in the current environment. |

### Why it matters

The feature lets developers collect basic network evidence and compare application behavior without pretending that a simulated network is the same as a real one.

**Important boundary:** It does not silently change routes, firewall rules, adapter configuration or kernel networking settings. ICMP probes may be filtered or rate-limited. A missing reply does not automatically prove that a host or route has failed.

---

## 📊 Experiments and Performance Evaluation

NetRescue is designed to compare network behavior under stated conditions rather than rely on a single overall score.

| Experiment | Variables | Results to examine |
|:--|:--|:--|
| Hysteresis sweep | Hysteresis ratio, heartbeat timing and configured failure conditions | State transitions, route flaps and recovery timing, where recorded |
| Scheduler comparison | Scheduler, retransmission policy and logical path conditions | Completion, duration, ordering and retransmission counts |
| Network diagnostics | Probe target, repetitions and probe count | Round-trip measurements, loss observations and route-trace results |
| Seeded fault scenarios | Fault type, seed and scenario settings | Repeatability and application behavior under the same declared scenario |

### Experimental discipline

- Keep real measurements separate from simulated results.
- State the configuration used for each comparison.
- Repeat experiments where practical.
- Report units and measurement limitations.
- Avoid claiming improvement unless the recorded results support it.
- Do not describe a seeded simulation as a real packet-level network experiment.

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11 or a compatible version supported by the project.
- Node.js and npm.
- Git.
- Windows PowerShell.
- Project dependencies installed from the repository's configuration files.

### 1️⃣ Clone the repository

```powershell
git clone <YOUR_NETRESCUE_GITHUB_URL>
cd NetRescue
```

If the repository is already on your computer, open PowerShell in its root directory instead.

### 2️⃣ Start the backend

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

If the virtual environment does not exist yet, create it and install the project's actual dependencies first:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Use the repository's documented dependency installation command or requirements file before starting the server.

### 3️⃣ Start the frontend

Open a second PowerShell terminal:

```powershell
cd apps/dashboard
npm install
npm run dev
```

Open the local URL printed by Vite. The port may differ if the default port is already occupied.

### 4️⃣ Verify the backend

Open these URLs if the server is running on port 8000:

- API documentation: `http://127.0.0.1:8000/docs`
- API root: `http://127.0.0.1:8000/`

The root endpoint may not provide a response unless it is implemented in the current backend.

### 5️⃣ Run tests

From the repository root, activate the virtual environment and run:

```powershell
python -m pytest
```

Use the project's documented frontend build or test commands to validate the dashboard. A successful build alone does not prove that every feature works correctly in a browser.

---

## ☁️ Deploying to Render

Hosted deployment (CORS origins, `PUBLIC_DEMO`, env vars, what can and cannot run remotely) is documented in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## 📁 Project Structure

```text
NetRescue/
├── apps/
│   ├── api/
│   │   ├── main.py
│   │   ├── deps.py
│   │   └── routers/
│   │       ├── nodes.py
│   │       ├── transfers.py
│   │       └── xfer.py
│   │
│   └── dashboard/
│       └── src/
│           ├── App.tsx
│           ├── pages/
│           └── multipath/
│
├── netrescue/
│   ├── core/
│   ├── recovery/
│   ├── transfer/
│   │   ├── scheduler.py
│   │   └── session.py
│   ├── benchmark/
│   └── emulation/
│
├── tests/
├── docker/
├── .gitignore
└── README.md
```

*This is a high-level guide, not a guaranteed exhaustive file listing. Update it if your repository structure differs.*

---

## 🔌 API Reference

The FastAPI application exposes REST endpoints for the functionality registered in the current backend.

| Area | Purpose |
|:--|:--|
| Nodes | Node-related operations supported by the API. |
| Transfers | Transfer operations supported by the registered transfer router. |
| Multipath transfer | Chunk-transfer experiments and results, where the Xfer router is registered. |
| Diagnostics | Windows network probes and controlled experiments, if registered in the current version. |
| Recovery | Hysteresis and recovery experiments, if registered in the current version. |

**Interactive API documentation:** `http://127.0.0.1:8000/docs`

Verify the actual HTTP methods, paths, request models and response schemas in the generated API documentation before publishing a detailed endpoint table.

---

## 🧪 Testing and Validation

NetRescue should be evaluated with both automated tests and live demonstrations.

- **Unit tests:** Verify state transitions, scheduler decisions, chunk offsets and other isolated logic.
- **API tests:** Check endpoint behavior, validation and error responses.
- **Failure scenarios:** Exercise missed heartbeats, recovery and unavailable-route conditions where implemented.
- **Transfer checks:** Confirm reconstruction and SHA-256 integrity.
- **Diagnostics checks:** Compare the displayed results with the Windows tools that produced them.
- **Frontend checks:** Verify loading, success, empty and error states in the browser.



---

## 🎯 Key Differentiators

### 🛡️ Stability over simple thresholds

The recovery experiment focuses on the trade-off between detecting failures and avoiding unnecessary state changes.

### 📦 Reliability beyond sending bytes

Chunk offsets, reassembly, retransmission and integrity checks make the transfer experiment more informative than a basic file-copy demonstration.

### 🔬 Honest measurement provenance

Real network probes and simulated fault scenarios are identified separately, helping users interpret the results correctly.

### 🪟 Practical Windows workflow

The diagnostics feature is designed to run without requiring Linux kernel network emulation or silently changing the host's network configuration.

---

## 🗺️ Scope and Limitations

NetRescue is an educational networking project and experimental dashboard. Its capabilities should be described according to the code and tests currently available.

| Capability | How to describe it |
|:--|:--|
| Hysteresis | State-machine recovery experiments; do not claim production-grade routing without end-to-end evidence. |
| Multipath transfer | Application-level scheduling over logical paths unless separate physical paths are actually implemented. |
| SHA-256 | Content-integrity verification, not encryption or authentication by itself. |
| Windows diagnostics | Real measurements from supported operating-system tools, subject to network conditions. |
| Fault simulation | Controlled application-level scenarios, not kernel-level packet shaping. |
| Performance results | Results for the tested workload and configuration, not universal guarantees. |

### 🔭 Future Improvements

- Add repeatable test scenarios with machine-readable result reports.
- Expand failure/restart tests and recovery-loop validation.
- Add more scheduler and retransmission comparisons.
- Improve diagnostic error handling for blocked ICMP and incomplete route traces.
- Investigate Mininet or another dedicated network emulator for more realistic topology and packet-level experiments.

---

<p align="center">
  <strong>Built with ❤️ to explore network reliability, recovery and file-transfer behavior.</strong>
</p>

<p align="center">
  <sub>NetRescue — observe the network, test the behavior, verify the results.</sub>
</p>
