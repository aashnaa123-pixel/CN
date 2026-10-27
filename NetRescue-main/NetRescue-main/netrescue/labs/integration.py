"""
netrescue/labs/integration.py — hysteresis → routing → transport, in one experiment.

The workflow:

  1. A heartbeat sequence for one monitored link is fed to the EXISTING
     HysteresisStateMachine (netrescue.recovery.state_machine) — the same class
     the recovery lab uses, with the same HEALTHY/SUSPECT/FAILED transitions.
  2. Each state change updates the routing graph: SUSPECT raises the link's cost
     (× suspect_cost_multiplier); FAILED marks it down; recovery to HEALTHY
     restores it. Dijkstra re-runs after every change.
  3. The route in use before, during and after the problem is turned into path
     parameters (RTT = 2 × Σ delay, loss = 1 − Π(1 − loss), bandwidth = min) and
     a TCP Reno transfer is SIMULATED over each.
  4. Metrics are returned with CSV for export.

Boundaries (what is and is not connected):
  * Heartbeats here are a supplied/seeded sequence, not live probes. The live
    overlay in the recovery lab is a separate, real-socket system.
  * Routing is a cost-based graph computation; it knows nothing about queues.
  * The transport step reduces a route to three numbers and runs the TCP model
    on them. It does not run the multipath file-transfer engine, and it does not
    claim that the chosen route is a physically independent path.
"""

from __future__ import annotations

import csv
import io
import random
from dataclasses import dataclass, field
from typing import Optional

from netrescue.labs.routing import (
    Graph, GraphError, Link, dijkstra, example_graph, link_id,
)
from netrescue.labs.tcp_cc import SimulationError, TcpParams, simulate_tcp
from netrescue.recovery.state_machine import HysteresisStateMachine, LinkState

MAX_TICKS = 400


class IntegrationError(ValueError):
    """Invalid experiment definition; message is safe to show to a user."""


@dataclass
class IntegrationParams:
    graph: Optional[Graph] = None
    source: str = "A"
    target: str = "F"
    monitored_link: str = "B-D"
    heartbeats: Optional[list[bool]] = None     # True = heartbeat MISSED
    total_ticks: int = 40
    outage_start: int = 8
    outage_len: int = 14
    f_thresh: int = 3
    n: int = 5
    r_thresh: int = 5
    use_ewma: bool = False
    ewma_alpha: float = 0.3
    suspect_cost_multiplier: float = 3.0
    degraded_loss: float = 0.03
    transfer_bytes: int = 3_000_000
    seed: int = 21


def _path_params(graph: Graph, path: list[str], override: dict[str, float]) -> dict:
    links = [graph.links[link_id(a, b)] for a, b in zip(path, path[1:])]
    delay = sum(l.delay_ms for l in links)
    ok = 1.0
    for l in links:
        ok *= 1.0 - max(l.loss, override.get(l.id, 0.0))
    return {
        "hops": len(links),
        "rtt_ms": round(max(0.1, 2 * delay), 3),
        "loss_rate": round(1.0 - ok, 6),
        "bandwidth_mbps": min(l.bandwidth_mbps for l in links),
    }


def _evaluate(name: str, description: str, graph: Graph, path: list[str],
              override: dict[str, float], p: IntegrationParams) -> dict:
    if not path:
        return {"phase": name, "description": description, "reachable": False,
                "path": [], "metrics": None}
    pp = _path_params(graph, path, override)
    try:
        run = simulate_tcp(TcpParams(
            rtt_ms=pp["rtt_ms"], bandwidth_mbps=min(pp["bandwidth_mbps"], 100.0),
            loss_rate=min(pp["loss_rate"], 0.49), transfer_bytes=p.transfer_bytes,
            duration_s=120.0, include_handshake=True, seed=p.seed))
    except SimulationError as exc:
        raise IntegrationError(str(exc)) from exc
    s = run["summary"]
    return {
        "phase": name, "description": description, "reachable": True, "path": path,
        "path_params": pp,
        "metrics": {
            "completed": s["completed"],
            "completion_time_s": s["completion_time_s"],
            "goodput_mbps": s["goodput_mbps"],
            "retransmissions": s["retransmissions"],
            "timeouts": s["timeouts"],
            "max_cwnd_segments": s["max_cwnd_segments"],
        },
        "cwnd_series": run["cwnd_series"],
    }


def run_integration(p: IntegrationParams) -> dict:
    graph = (p.graph or example_graph()).copy()
    if p.source not in graph.nodes or p.target not in graph.nodes:
        raise IntegrationError("source and target must be nodes in the graph")
    if p.source == p.target:
        raise IntegrationError("source and target must differ")
    if p.monitored_link not in graph.links:
        raise IntegrationError(f"monitored_link {p.monitored_link!r} is not a link in the graph")
    if not 1.0 <= p.suspect_cost_multiplier <= 1000:
        raise IntegrationError("suspect_cost_multiplier must be between 1 and 1000")
    if not 0.0 <= p.degraded_loss < 0.5:
        raise IntegrationError("degraded_loss must be in [0, 0.5)")
    if not 10_000 <= p.transfer_bytes <= 50_000_000:
        raise IntegrationError("transfer_bytes must be between 10000 and 50000000")

    if p.heartbeats is not None:
        beats = list(p.heartbeats)
    else:
        if not 5 <= p.total_ticks <= MAX_TICKS:
            raise IntegrationError(f"total_ticks must be between 5 and {MAX_TICKS}")
        if p.outage_start < 0 or p.outage_len < 0:
            raise IntegrationError("outage_start and outage_len must be >= 0")
        beats = [p.outage_start <= i < p.outage_start + p.outage_len
                 for i in range(p.total_ticks)]
    if not 1 <= len(beats) <= MAX_TICKS:
        raise IntegrationError(f"heartbeat sequence must have 1..{MAX_TICKS} entries")

    try:
        sm = HysteresisStateMachine(
            link_id=p.monitored_link, f_thresh=p.f_thresh, n=p.n, r_thresh=p.r_thresh,
            use_ewma=p.use_ewma, ewma_alpha=p.ewma_alpha)
    except TypeError as exc:
        raise IntegrationError(str(exc)) from exc
    if not (1 <= p.f_thresh < p.n) or p.r_thresh < 1:
        raise IntegrationError("thresholds must satisfy 1 <= f_thresh < n and r_thresh >= 1")

    a, b = p.monitored_link.split("-", 1)
    base_cost = graph.links[p.monitored_link].cost
    original = graph.copy()
    baseline = dijkstra(original, p.source, p.target)

    work = graph.copy()
    current = baseline
    timeline = [{"tick": 0, "state": "HEALTHY", "action": "initial route",
                 "path": baseline.path, "cost": baseline.cost, "rerouted": False}]
    states: list[str] = []
    worst_rank = {LinkState.HEALTHY: 0, LinkState.SUSPECT: 1, LinkState.FAILED: 2}
    worst_state, worst_route, worst_graph = LinkState.HEALTHY, baseline, original.copy()
    prev_state = sm.state
    route_changes = 0

    for tick, missed in enumerate(beats, start=1):
        state = sm.update(missed)
        states.append(state.value)
        if state == prev_state:
            continue
        prev_state = state
        if state == LinkState.SUSPECT:
            work.set_up(a, b, True)
            work.set_cost(a, b, base_cost * p.suspect_cost_multiplier)
            action = f"cost × {p.suspect_cost_multiplier:g} → {base_cost * p.suspect_cost_multiplier:g}"
        elif state == LinkState.FAILED:
            work.set_up(a, b, False)
            action = "link marked down"
        else:
            work.set_up(a, b, True)
            work.set_cost(a, b, base_cost)
            action = f"link restored (cost {base_cost:g})"
        new_route = dijkstra(work, p.source, p.target)
        changed = new_route.path != current.path
        route_changes += int(changed)
        timeline.append({"tick": tick, "state": state.value, "action": action,
                         "path": new_route.path, "cost": new_route.cost,
                         "reachable": new_route.reachable, "rerouted": changed})
        current = new_route
        if worst_rank[state] > worst_rank[worst_state]:
            worst_state, worst_route, worst_graph = state, new_route, work.copy()

    naive_changes = sum(1 for x, y in zip(beats, beats[1:]) if x != y)

    degraded_override = {p.monitored_link: p.degraded_loss}
    phases = [
        _evaluate("baseline", "Healthy network, original shortest route.",
                  original, baseline.path, {}, p),
        _evaluate("degraded_not_rerouted",
                  f"Original route while {p.monitored_link} is degraded "
                  f"(loss ≥ {p.degraded_loss:g}) and traffic is NOT moved.",
                  original, baseline.path, degraded_override, p),
        _evaluate("after_reroute",
                  "Route chosen after the hysteresis detector reached "
                  f"{worst_state.value}" + ("" if worst_route.path != baseline.path
                                           else " (route did not change)") + ".",
                  worst_graph, worst_route.path, {}, p),
    ]

    outcome = _summarise(p, phases, worst_state, route_changes, naive_changes, sm)
    result = {
        "kind": "simulation",
        "label": ("SIMULATED — heartbeats are a supplied sequence, routing is a graph "
                  "computation, transport is the TCP Reno model; nothing was measured on a network"),
        "params": {
            "source": p.source, "target": p.target, "monitored_link": p.monitored_link,
            "f_thresh": p.f_thresh, "n": p.n, "r_thresh": p.r_thresh,
            "use_ewma": p.use_ewma, "ewma_alpha": p.ewma_alpha,
            "suspect_cost_multiplier": p.suspect_cost_multiplier,
            "degraded_loss": p.degraded_loss, "transfer_bytes": p.transfer_bytes,
            "seed": p.seed, "ticks": len(beats),
        },
        "graph": graph.to_dict(),
        "heartbeats": beats,
        "states": states,
        "timeline": timeline,
        "phases": phases,
        "outcome": outcome,
        "boundaries": [
            "Heartbeats are a supplied or generated sequence, not live probes; the live overlay is the separate Recovery lab.",
            "Routing is cost-based and has no knowledge of queues or congestion.",
            "Transport reduces a route to RTT, loss and bottleneck bandwidth, then runs the TCP Reno model; it does not run the multipath transfer engine.",
            "A 'route' here is a path through a graph, not a physically independent network path.",
        ],
    }
    result["csv"] = to_csv(result)
    return result


def _summarise(p, phases, worst_state, route_changes, naive_changes, sm) -> dict:
    base, deg, after = (ph["metrics"] for ph in phases)
    out = {
        "deepest_state": worst_state.value,
        "route_changes_hysteresis": route_changes,
        "heartbeat_toggles_naive": naive_changes,
        "hysteresis_route_flaps": sm.route_flap_count,
        "notes": [],
    }
    if base and deg:
        out["degraded_vs_baseline_time_ratio"] = (
            round(deg["completion_time_s"] / base["completion_time_s"], 3)
            if base["completion_time_s"] and deg["completion_time_s"] else None)
    if deg and after and deg["completion_time_s"] and after["completion_time_s"]:
        out["reroute_speedup_vs_degraded"] = round(
            deg["completion_time_s"] / after["completion_time_s"], 3)
        out["notes"].append(
            f"Transfer of {p.transfer_bytes} bytes: {deg['completion_time_s']} s on the degraded "
            f"original route vs {after['completion_time_s']} s after rerouting.")
    if phases[2]["path"] == phases[0]["path"]:
        out["notes"].append("The detector did not trigger a route change, so before and after are the same route.")
    if not phases[2]["reachable"]:
        out["notes"].append("No route to the target exists once the link is down.")
    out["notes"].append(
        f"A threshold-free detector would have toggled {naive_changes} times; the hysteresis "
        f"detector changed the route {route_changes} time(s).")
    return out


def to_csv(result: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["phase", "reachable", "path", "hops", "rtt_ms", "loss_rate", "bandwidth_mbps",
                "completed", "completion_time_s", "goodput_mbps", "retransmissions", "timeouts"])
    for ph in result["phases"]:
        pp, m = ph.get("path_params") or {}, ph.get("metrics") or {}
        w.writerow([ph["phase"], ph["reachable"], "-".join(ph["path"]), pp.get("hops", ""),
                    pp.get("rtt_ms", ""), pp.get("loss_rate", ""), pp.get("bandwidth_mbps", ""),
                    m.get("completed", ""), m.get("completion_time_s", ""),
                    m.get("goodput_mbps", ""), m.get("retransmissions", ""), m.get("timeouts", "")])
    return buf.getvalue()
