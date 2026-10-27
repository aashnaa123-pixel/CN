"""
netrescue/labs/execution.py — the backend's statement of what is REAL and what is SIMULATED.

Every operation the platform can run is registered here with:

  mode         "REAL" or "SIMULATION" — what the operation is
  symbol       the code that implements it (module or module:function)
  primitives   for REAL operations: calls that must appear in that code
               (a subprocess, a socket, a resolver call)
  forbidden    for SIMULATION operations: calls that must NOT appear in that
               code (no sockets, no subprocesses, no resolver)

`verify()` reads the implementing source and checks those claims, so an
operation is only reported as REAL if its implementation really contains an OS
or network primitive, and as SIMULATION only if its implementation contains
none. If a claim fails, the operation is reported as UNVERIFIED rather than
trusting the label. The classification therefore comes from the backend and
the code, never from the frontend.

`attach(payload, op_id, ...)` adds an `execution` block to an API response with
the verified mode plus run-specific facts (seed, inputs, target, outcome).
tests/test_execution_modes.py enforces the registry against the source.

REAL means: the operation was performed against the operating system or a real
socket on this machine. Loopback sockets are REAL sockets, but they do not
cross a physical network; that is stated in each entry's `limitations`.
"""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Optional

REAL = "REAL"
SIMULATION = "SIMULATION"
MIXED = "MIXED"
UNVERIFIED = "UNVERIFIED"

#: Calls whose presence means code touches the OS / network.
NETWORK_PRIMITIVES = (
    "socket.socket(", "socket.create_connection(", "subprocess.", "create_subprocess",
    "asyncio.start_server", "asyncio.open_connection", "create_datagram_endpoint",
    "getaddrinfo(",
)


@dataclass(frozen=True)
class Operation:
    id: str
    feature: str
    title: str
    mode: str
    what: str                         # what is actually executed
    symbol: str                       # "package.module" or "package.module:name"
    primitives: tuple[str, ...] = ()  # REAL: at least one must appear in the source
    limitations: tuple[str, ...] = ()
    model: str = ""                   # SIMULATION: the model/algorithm


_OPS: list[Operation] = [
    # -- Feature A: hysteresis recovery --------------------------------------
    Operation("overlay.live", "hysteresis", "Live forwarding overlay", REAL,
              "Starts real asyncio TCP listeners on 127.0.0.1 and forwards real packets between them; "
              "a node failure closes its listener; delivery is confirmed by the destination's acknowledgement.",
              "netrescue.forwarding.node",
              ("asyncio.start_server", "asyncio.open_connection"),
              ("Loopback only: every node runs on this machine, so links have no physical delay or loss.",)),
    Operation("overlay.detector_comparison", "hysteresis", "Hysteresis vs naive detector comparison", SIMULATION,
              "Replays one supplied heartbeat sequence through both detectors and counts transitions.",
              "netrescue.playground:run_detector_comparison", (),
              ("The heartbeat sequence is supplied by the user; no link is changed and no packet is sent.",),
              model="HysteresisStateMachine vs single-threshold detector over a supplied sequence"),
    # -- Feature B/C: multipath transfer --------------------------------------
    Operation("transfer.multipath", "multipath", "Multipath file transfer", SIMULATION,
              "Chunks the uploaded bytes, schedules chunks over logical paths with configured delay/loss, "
              "reassembles and verifies SHA-256. The file bytes and hashing are real; the paths are not.",
              "netrescue.transfer.session", (),
              ("A path is a modelled delay-and-loss profile inside one process, not a socket, interface or cable.",
               "Nothing leaves this machine."),
              model="MultipathTransferSession over in-process logical paths"),
    Operation("transfer.scheduler_compare", "multipath", "Scheduler comparison", SIMULATION,
              "Runs FIFO and priority schedulers over identical generated arrivals.",
              "netrescue.traffic.experiment", (),
              ("Queueing-model output, not a measurement of a wire.",),
              model="discrete-event queueing model"),
    # -- Feature D: diagnostics ------------------------------------------------
    Operation("diag.ping", "diagnostics", "Ping", REAL,
              "Runs the operating system's ping command (argument list, no shell) and parses its output.",
              "netrescue.diagnostics.probes", ("subprocess.run(",),
              ("ICMP may be blocked by firewalls; a target with zero replies is reported as unreachable.",)),
    Operation("diag.traceroute", "diagnostics", "Route trace", REAL,
              "Runs the operating system's tracert/traceroute command and parses the hops.",
              "netrescue.diagnostics.probes", ("subprocess.run(",),
              ("Hops that do not answer ICMP appear as timeouts.",)),
    Operation("diag.dns", "diagnostics", "DNS resolution", REAL,
              "Resolves a name through the operating system resolver (socket.getaddrinfo).",
              "netrescue.diagnostics.probes", ("getaddrinfo(",),
              ("Answers may come from the OS resolver cache.",)),
    Operation("diag.network", "diagnostics", "Adapters and gateway", REAL,
              "Reads adapter, IP and gateway configuration from the operating system.",
              "netrescue.diagnostics.probes", ("subprocess.run(",),
              ("Read-only; no interface or route is modified.",)),
    Operation("diag.benchmark", "diagnostics", "Repeated ping benchmark", REAL,
              "Repeats real ping probes and aggregates latency and loss statistics.",
              "netrescue.diagnostics.runner:BenchmarkEngine", ("ping, spec.target",),
              ("Statistics describe the path to the chosen target at the time of the run.",)),
    Operation("diag.fault_scenario", "diagnostics", "Fault scenario", SIMULATION,
              "Replays a seeded fault timeline (loss, jitter, timeout, intermittent) through a probe model.",
              "netrescue.diagnostics.faults:run_fault_scenario", (),
              ("No packet is sent; faults are generated by the scenario definition.",),
              model="seeded fault scenario"),
    Operation("diag.hysteresis_sweep", "diagnostics", "Hysteresis sweep", SIMULATION,
              "Replays one identical seeded outcome sequence through detectors at several hysteresis ratios.",
              "netrescue.benchmark.sweep", (),
              ("The outcome sequence is generated, not measured.",),
              model="seeded sequence replay over HysteresisStateMachine"),
    Operation("diag.scheduler_comparison", "diagnostics", "Scheduler comparison", SIMULATION,
              "Runs scheduler variants over declared logical paths with the same payload.",
              "netrescue.transfer.session", (),
              ("Declared path profiles, not live paths.",),
              model="MultipathTransferSession over in-process logical paths"),
    # -- Networking labs -------------------------------------------------------
    Operation("routing.dijkstra", "routing", "Dijkstra shortest path", SIMULATION,
              "Runs Dijkstra's algorithm on the supplied graph and records every iteration.",
              "netrescue.labs.routing", (),
              ("A graph computation; it does not change any real routing table.",),
              model="Dijkstra (+ Yen k-shortest paths)"),
    Operation("tcp.reno", "tcp", "TCP Reno congestion model", SIMULATION,
              "Discrete-event simulation of one TCP Reno flow over one bottleneck.",
              "netrescue.labs.tcp_cc", (),
              ("Not a kernel experiment; one variant, one flow, one bottleneck.",),
              model="TCP Reno (RFC 5681) + RFC 6298 timer"),
    Operation("shaping.buckets", "shaping", "Token bucket vs leaky bucket", SIMULATION,
              "Runs both shapers over one seeded packet-arrival list.",
              "netrescue.labs.shaping", (),
              ("Models the algorithms; does not shape real traffic.",),
              model="token bucket / leaky bucket with tail-drop queue"),
    Operation("tcpudp.loopback", "tcp-udp", "TCP and UDP loopback experiment", REAL,
              "Opens real TCP and UDP sockets on 127.0.0.1 and times connect, round trips and bulk transfer.",
              "netrescue.labs.tcp_udp", ("socket.socket(", "socket.create_connection("),
              ("Loopback has no propagation delay, bandwidth limit or normal loss; no impairment is injected.",)),
    Operation("tcpudp.simulated", "tcp-udp", "TCP vs UDP model", SIMULATION,
              "Reno model versus a paced UDP sender over one modelled lossy bottleneck.",
              "netrescue.labs.tcp_udp:run_simulated", (),
              ("No sockets are used; results come from the model and its seed.",),
              model="TCP Reno vs paced UDP over a drop-tail bottleneck"),
    Operation("integration.run", "integration", "Detect → reroute → transfer", SIMULATION,
              "Feeds a generated heartbeat sequence to the real HysteresisStateMachine, recalculates routes with "
              "Dijkstra and simulates the transfer with the TCP model.",
              "netrescue.labs.integration", (),
              ("Heartbeats are generated, not probed; no operating-system route is changed.",),
              model="HysteresisStateMachine → Dijkstra → TCP Reno"),
]

REGISTRY: dict[str, Operation] = {o.id: o for o in _OPS}


@lru_cache(maxsize=None)
def _source_of(symbol: str) -> str:
    module_name, _, name = symbol.partition(":")
    module = importlib.import_module(module_name)
    target: Any = module
    if name:
        for part in name.split("."):
            target = getattr(target, part)
    return inspect.getsource(target)


@lru_cache(maxsize=None)
def verify(op_id: str) -> tuple[bool, str]:
    """Check an operation's claim against its implementing source."""
    op = REGISTRY[op_id]
    try:
        src = _source_of(op.symbol)
    except Exception as exc:                    # missing symbol/module = unverifiable
        return False, f"could not read {op.symbol}: {exc}"
    if op.mode == REAL:
        hits = [p for p in op.primitives if p in src]
        if not hits:
            return False, f"{op.symbol} contains none of {list(op.primitives)}"
        return True, f"{op.symbol} calls {', '.join(hits)}"
    hits = [p for p in NETWORK_PRIMITIVES if p in src]
    if hits:
        return False, f"{op.symbol} contains network primitives {hits}"
    return True, f"{op.symbol} contains no socket, subprocess or resolver calls"


def describe(op_id: str) -> dict:
    op = REGISTRY[op_id]
    ok, evidence = verify(op_id)
    return {
        "id": op.id, "feature": op.feature, "title": op.title,
        "mode": op.mode if ok else UNVERIFIED,
        "declared_mode": op.mode,
        "operation": op.what, "implementation": op.symbol,
        "evidence": evidence, "model": op.model or None,
        "limitations": list(op.limitations),
    }


def feature_mode(feature: str) -> str:
    modes = {describe(o.id)["mode"] for o in REGISTRY.values() if o.feature == feature}
    if not modes:
        raise KeyError(feature)
    if UNVERIFIED in modes:
        return UNVERIFIED
    return modes.pop() if len(modes) == 1 else MIXED


def profile() -> dict:
    """Every feature with its derived mode and verified operations."""
    features: dict[str, dict] = {}
    for op in REGISTRY.values():
        f = features.setdefault(op.feature, {"operations": []})
        f["operations"].append(describe(op.id))
    for name, f in features.items():
        f["mode"] = feature_mode(name)
    return features


def attach(payload: dict, op_id: str, **run: Any) -> dict:
    """
    Add the backend-established `execution` block to a response payload.

    `run` carries run-specific facts: seed, inputs, target, outcome. The mode and
    evidence come from verify(), not from the caller.
    """
    info = describe(op_id)
    info["executed_at"] = datetime.now(timezone.utc).isoformat()
    info.update({k: v for k, v in run.items() if v is not None})
    payload["execution"] = info
    return payload
