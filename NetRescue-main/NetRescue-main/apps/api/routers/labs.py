"""
apps/api/routers/labs.py — networking labs (Features E–H) and the integration lab.

  GET  /api/labs/info                    models, assumptions, module boundaries
  GET  /api/labs/routing/example         the deterministic example topology
  POST /api/labs/routing/solve           Dijkstra route, trace and alternatives
  POST /api/labs/routing/whatif          before/after for link failure or cost change
  POST /api/labs/tcp/simulate            one TCP Reno scenario
  POST /api/labs/tcp/compare             several scenarios side by side
  POST /api/labs/shaping/simulate        token bucket vs leaky bucket, same workload
  POST /api/labs/tcp-udp/simulate        simulated TCP vs UDP (no sockets)
  POST /api/labs/tcp-udp/real            real loopback sockets (operator only)
  POST /api/labs/integration/run         hysteresis → routing → transport

Every response carries `kind`: "simulation" or "measurement". Only
/tcp-udp/real is a measurement, and it says exactly what it measured. All
simulation logic lives in `netrescue.labs`; this module only validates input
and maps domain errors to HTTP status codes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from apps.api.deps import RequireOperator, RequireRead
from netrescue.labs import execution, integration, routing, shaping, tcp_cc, tcp_udp

router = APIRouter(prefix="/api/labs", tags=["networking-labs"])
log = logging.getLogger(__name__)

_DOMAIN_ERRORS = (
    routing.GraphError, tcp_cc.SimulationError, shaping.ShapingError,
    tcp_udp.LabError, integration.IntegrationError,
)


def _stamp(payload: dict) -> dict:
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    return payload


def _guard(fn):
    """Run a lab function, turning domain validation errors into 422s."""
    try:
        return fn()
    except tcp_udp.LabBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except _DOMAIN_ERRORS as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LinkIn(BaseModel):
    a: str = Field(min_length=1, max_length=24)
    b: str = Field(min_length=1, max_length=24)
    cost: float = Field(gt=0, allow_inf_nan=False, description="Unitless administrative cost")
    delay_ms: float = Field(5.0, ge=0, le=5000, allow_inf_nan=False)
    loss: float = Field(0.0, ge=0, lt=1, allow_inf_nan=False)
    bandwidth_mbps: float = Field(100.0, gt=0, le=100_000, allow_inf_nan=False)
    up: bool = True


class GraphIn(BaseModel):
    nodes: list[str] = Field(max_length=routing.MAX_NODES)
    links: list[LinkIn] = Field(max_length=routing.MAX_LINKS)

    def build(self) -> routing.Graph:
        return routing.Graph.from_dict(self.model_dump())


class SolveRequest(BaseModel):
    graph: GraphIn
    source: str
    target: str
    alternatives: int = Field(3, ge=1, le=6)


class ChangeIn(BaseModel):
    kind: str = Field(description="link_down | link_up | cost_change")
    a: str
    b: str
    cost: Optional[float] = Field(None, gt=0, allow_inf_nan=False)


class WhatIfRequest(SolveRequest):
    changes: list[ChangeIn] = Field(min_length=1, max_length=20)


class TcpIn(BaseModel):
    rtt_ms: float = Field(80.0, ge=0.1, le=2000)
    bandwidth_mbps: float = Field(10.0, ge=0.1, le=100)
    loss_rate: float = Field(0.0, ge=0, lt=0.5)
    buffer_packets: int = Field(40, ge=1, le=5000)
    duration_s: float = Field(20.0, ge=0.5, le=120)
    mss_bytes: int = Field(1460, ge=100, le=9000)
    initial_cwnd: int = Field(1, ge=1, le=100)
    initial_ssthresh: int = Field(64, ge=2, le=10000)
    min_rto_ms: float = Field(200.0, ge=1, le=5000)
    transfer_bytes: Optional[int] = Field(None, ge=1460, le=500_000_000)
    include_handshake: bool = False
    seed: int = Field(1, ge=0, le=2**31 - 1)
    variant: str = "reno"

    def params(self) -> tcp_cc.TcpParams:
        return tcp_cc.TcpParams(**self.model_dump(exclude={"label"}))


class TcpScenarioIn(TcpIn):
    label: str = Field("scenario", max_length=40)


class TcpCompareRequest(BaseModel):
    scenarios: list[TcpScenarioIn] = Field(min_length=1, max_length=6)


class ShapingIn(BaseModel):
    rate_bps: float = Field(1_000_000.0, ge=1_000, le=1e9)
    bucket_bytes: int = Field(6_000, ge=1, le=10_000_000)
    queue_packets: int = Field(20, ge=1, le=5000)
    packet_bytes: int = Field(1000, ge=1, le=9000)
    duration_s: float = Field(5.0, ge=0.1, le=120)
    pattern: str = "burst"
    arrival_rate_bps: float = Field(800_000.0, ge=1_000, le=1e9)
    burst_packets: int = Field(15, ge=1, le=5000)
    burst_interval_s: float = Field(1.0, ge=0.01, le=120)
    burst_peak_bps: float = Field(8_000_000.0, ge=1_000, le=1e9)
    seed: int = Field(7, ge=0, le=2**31 - 1)


class SimTcpUdpIn(BaseModel):
    rtt_ms: float = Field(60.0, ge=0.1, le=2000)
    bandwidth_mbps: float = Field(10.0, ge=0.1, le=100)
    loss_rate: float = Field(0.01, ge=0, lt=0.5)
    buffer_packets: int = Field(40, ge=1, le=5000)
    transfer_bytes: int = Field(2_000_000, ge=10_000, le=50_000_000)
    udp_send_rate_mbps: float = Field(8.0, ge=1, le=100)
    packet_bytes: int = Field(1460, ge=100, le=9000)
    seed: int = Field(11, ge=0, le=2**31 - 1)


class RealTcpUdpIn(BaseModel):
    ping_count: int = Field(200, ge=10, le=2000)
    payload_bytes: int = Field(512, ge=8, le=1400)
    bulk_bytes: int = Field(8_000_000, ge=100_000, le=64_000_000)
    udp_datagrams: int = Field(5000, ge=100, le=50_000)
    udp_datagram_bytes: int = Field(1000, ge=8, le=1400)


class IntegrationIn(BaseModel):
    graph: Optional[GraphIn] = None
    source: str = "A"
    target: str = "F"
    monitored_link: str = "B-D"
    heartbeats: Optional[list[bool]] = Field(
        None, max_length=integration.MAX_TICKS,
        description="true = heartbeat missed. Omit to generate an outage.")
    total_ticks: int = Field(40, ge=5, le=integration.MAX_TICKS)
    outage_start: int = Field(8, ge=0, le=integration.MAX_TICKS)
    outage_len: int = Field(14, ge=0, le=integration.MAX_TICKS)
    f_thresh: int = Field(3, ge=1, le=50)
    n: int = Field(5, ge=2, le=100)
    r_thresh: int = Field(5, ge=1, le=100)
    use_ewma: bool = False
    ewma_alpha: float = Field(0.3, gt=0, le=1)
    suspect_cost_multiplier: float = Field(3.0, ge=1, le=1000)
    degraded_loss: float = Field(0.03, ge=0, lt=0.5)
    transfer_bytes: int = Field(3_000_000, ge=10_000, le=50_000_000)
    seed: int = Field(21, ge=0, le=2**31 - 1)


# ---------------------------------------------------------------------------
# Info
# ---------------------------------------------------------------------------


@router.get("/info")
def info(_: RequireRead):
    return {
        "modules": [
            {"id": "routing", "title": "Shortest-Path Routing", "kind": "simulation",
             "algorithm": "Dijkstra (alternatives: Yen's k-shortest paths)"},
            {"id": "tcp", "title": "TCP Congestion Control", "kind": "simulation",
             "algorithm": "TCP Reno, discrete-event"},
            {"id": "shaping", "title": "QoS / Traffic Shaping", "kind": "simulation",
             "algorithm": "Token bucket vs leaky bucket"},
            {"id": "tcp-udp", "title": "TCP vs UDP", "kind": "simulation + measurement",
             "algorithm": "Real loopback sockets, or Reno vs paced UDP simulation"},
            {"id": "integration", "title": "Hysteresis → Routing → Transport", "kind": "simulation",
             "algorithm": "Existing HysteresisStateMachine feeding Dijkstra and the TCP model"},
        ],
        "boundaries": integration_boundaries(),
        "tcp_variants": list(tcp_cc.SUPPORTED_VARIANTS),
        "traffic_patterns": list(shaping.PATTERNS),
        "protocol_notes": tcp_udp.PROTOCOL_NOTES,
    }


@router.get("/execution")
def execution_profile(_: RequireRead):
    """
    Which operations are REAL and which are SIMULATION, as established by the
    backend: each claim is checked against the implementing source code
    (see netrescue.labs.execution). The UI reads this; it does not decide.
    """
    return {"features": execution.profile(),
            "definitions": {
                "REAL": "The operation is performed against the operating system or a real socket on this machine.",
                "SIMULATION": "The result is produced by a model or algorithm; no corresponding live network operation occurs.",
                "MIXED": "The feature contains both kinds; each result section is labelled separately.",
            }}


def integration_boundaries() -> list[str]:
    return [
        "Routing: cost-based graph computation; it knows nothing about queues, loss or congestion.",
        "Transport (TCP lab): one flow over one bottleneck described by RTT, loss and bandwidth.",
        "Shaping: token/leaky bucket on a packet list; independent of TCP behaviour.",
        "TCP vs UDP: real loopback sockets OR a separate simulation, labelled in every result.",
        "Integration: a route is reduced to RTT, loss and bottleneck bandwidth, then run through the TCP model.",
        "The multipath file-transfer engine and the live recovery overlay remain separate systems.",
    ]


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@router.get("/routing/example")
def routing_example(_: RequireRead):
    g = routing.example_graph()
    return {"graph": g.to_dict(), "source": "A", "target": "F",
            "suggested_change": {"kind": "link_down", "a": "B", "b": "D"}}


@router.post("/routing/solve")
def routing_solve(body: SolveRequest, principal: RequireOperator):
    def go():
        g = body.graph.build()
        if body.target not in g.nodes:
            raise routing.GraphError(f"unknown destination node {body.target!r}")
        # One FULL run from the source: its recorded iterations and final state
        # feed the iteration table, the routing table and the selected route.
        full = routing.dijkstra(g, body.source)
        path, links = full.path_to(body.target)
        reachable = bool(path)
        route = routing.RouteResult(
            source=body.source, target=body.target, reachable=reachable, path=path,
            links=links, cost=full.distances[body.target] if reachable else None,
            visited=full.visited, distances=full.distances, trace=full.trace,
            predecessors=full.predecessors, pred_links=full.pred_links,
            iterations=full.iterations)
        return execution.attach(_stamp({
            "kind": "simulation", "route": route.to_dict(),
            "iterations": [i.to_dict() for i in full.iterations],
            "routing_table": routing.routing_table(full),
            "alternatives": routing.k_shortest_paths(g, body.source, body.target,
                                                     body.alternatives),
            "tie_breaking": "Equal tentative distances are resolved by the lowest node name.",
            "cost_semantics": "Unitless administrative cost; the route with the smallest sum wins.",
            "terminology": ("Dijkstra computes shortest paths; the routing table is derived from its "
                            "result. It is not a routing protocol and changes no real routing table."),
        }), "routing.dijkstra", inputs={"source": body.source, "target": body.target,
                                        "nodes": len(g.nodes), "links": len(g.links)})
    return _guard(go)


@router.post("/routing/whatif")
def routing_whatif(body: WhatIfRequest, principal: RequireOperator):
    def go():
        g = body.graph.build()
        out = routing.compare_routes(g, body.source, body.target,
                                     [c.model_dump(exclude_none=True) for c in body.changes],
                                     body.alternatives)
        out["kind"] = "simulation"
        return execution.attach(_stamp(out), "routing.dijkstra",
                                inputs={"changes": [c.model_dump(exclude_none=True) for c in body.changes],
                                        "source": body.source, "target": body.target})
    return _guard(go)


# ---------------------------------------------------------------------------
# TCP congestion control
# ---------------------------------------------------------------------------


@router.post("/tcp/simulate")
def tcp_simulate(body: TcpIn, principal: RequireOperator):
    return _guard(lambda: execution.attach(
        _stamp(tcp_cc.simulate_tcp(body.params())), "tcp.reno",
        seed=body.seed, inputs=body.model_dump()))


@router.post("/tcp/compare")
def tcp_compare(body: TcpCompareRequest, principal: RequireOperator):
    def go():
        scenarios = [(s.label, s.params()) for s in body.scenarios]
        out = tcp_cc.compare_scenarios(scenarios)
        out["kind"] = "simulation"
        return execution.attach(_stamp(out), "tcp.reno",
                                inputs={"scenarios": [s.model_dump() for s in body.scenarios]})
    return _guard(go)


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------


@router.post("/shaping/simulate")
def shaping_simulate(body: ShapingIn, principal: RequireOperator):
    return _guard(lambda: execution.attach(_stamp(
        shaping.compare_shapers(shaping.ShapingParams(**body.model_dump()))),
        "shaping.buckets", seed=body.seed, inputs=body.model_dump()))


# ---------------------------------------------------------------------------
# TCP vs UDP
# ---------------------------------------------------------------------------


@router.post("/tcp-udp/simulate")
def tcp_udp_simulate(body: SimTcpUdpIn, principal: RequireOperator):
    out = _guard(lambda: _stamp(tcp_udp.run_simulated(tcp_udp.SimParams(**body.model_dump()))))
    out["protocol_notes"] = tcp_udp.PROTOCOL_NOTES
    return execution.attach(out, "tcpudp.simulated", seed=body.seed, inputs=body.model_dump())


@router.post("/tcp-udp/real")
def tcp_udp_real(body: RealTcpUdpIn, principal: RequireOperator):
    """Real sockets on 127.0.0.1 only. Binds loopback, never a routable address."""
    log.info("[labs] %s started a real loopback TCP/UDP experiment", principal.name)
    out = _guard(lambda: _stamp(tcp_udp.run_real_loopback(tcp_udp.RealParams(**body.model_dump()))))
    out["protocol_notes"] = tcp_udp.PROTOCOL_NOTES
    errors = out["tcp"].get("errors", []) + out["udp"].get("errors", [])
    return execution.attach(
        out, "tcpudp.loopback", target="127.0.0.1 (loopback; ephemeral ports)",
        inputs=body.model_dump(),
        outcome="completed with errors: " + "; ".join(errors) if errors else "completed")


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


@router.post("/integration/run")
def integration_run(body: IntegrationIn, principal: RequireOperator):
    def go():
        data = body.model_dump(exclude={"graph"})
        graph = body.graph.build() if body.graph else None
        return execution.attach(_stamp(integration.run_integration(
            integration.IntegrationParams(graph=graph, **data))),
            "integration.run", seed=body.seed, inputs=data)
    return _guard(go)
