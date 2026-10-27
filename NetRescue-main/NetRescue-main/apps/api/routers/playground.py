"""
apps/api/routers/playground.py — Feature A's interactive workbench API.

  GET    /api/playground/state                 everything the canvas renders
  PUT    /api/playground/topology              save the topology that was drawn
  POST   /api/playground/topology/example      load the starting example
  POST   /api/playground/topology/clear        empty the workspace
  POST   /api/playground/validate              check it without starting anything
  POST   /api/playground/apply                 start real forwarding nodes
  POST   /api/playground/teardown              stop them
  POST   /api/playground/test                  send a packet and report delivery
  POST   /api/playground/nodes/{id}/fail       stop that node's listener
  POST   /api/playground/nodes/{id}/restore    start it again
  POST   /api/playground/links/{id}/fail       mark that link down everywhere
  POST   /api/playground/links/{id}/restore    bring it back
  GET    /api/playground/detectors             live detector readings
  POST   /api/playground/detectors             retune one link's detector
  GET    /api/playground/transitions           transitions the nodes really made
  POST   /api/playground/hysteresis/compare    hysteresis vs naive, same input
  GET    /api/playground/events                the experiment journal
  POST   /api/playground/events/clear          empty it
  GET    /api/playground/export                the log, as JSON or CSV

The rule this router exists to keep: what the browser draws and what the network
does are the same graph. Every response here is produced by the runtime that
carries the packets, and a failure is only reported once the runtime has applied
it — `fail_node` closes a real listener, `fail_link` rebuilds every forwarding
table so no next hop still crosses it, and `test` returns the destination's own
acknowledgement or says plainly that there was none.

Mutations require the operator role and are audited, exactly like the rest of
the control plane.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from apps.api.deps import RequireOperator, RequireRead
from apps.api.runtime import lab_service, playground_service
from netrescue.forwarding.node import link_name
from netrescue.lab import MAX_NODES
from netrescue.playground import (
    VALID_ROLES,
    PlaygroundTopology,
    TopologyLinkSpec,
    TopologyNodeSpec,
    example_topology,
    run_detector_comparison,
    validate_topology,
)
from netrescue.labs import execution
from netrescue.security.audit import default_auditor

router = APIRouter(prefix="/api/playground", tags=["playground"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class NodeInput(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=32)
    label: str = Field("", max_length=64)
    role: Literal["source", "router", "destination"] = "router"
    x: float = 0.0
    y: float = 0.0


class LinkInput(BaseModel):
    node_a: str = Field(..., min_length=1, max_length=32)
    node_b: str = Field(..., min_length=1, max_length=32)


class TopologyInput(BaseModel):
    nodes: list[NodeInput] = Field(default_factory=list, max_length=64)
    links: list[LinkInput] = Field(default_factory=list, max_length=256)
    source: str | None = None
    destination: str | None = None


class ValidateRequest(BaseModel):
    source: str | None = None
    destination: str | None = None


class ApplyRequest(BaseModel):
    heartbeat_interval_s: float = Field(0.5, gt=0.05, le=30.0)
    f_thresh: int = Field(3, ge=1, le=50)
    n: int = Field(5, ge=1, le=100)
    r_thresh: int = Field(5, ge=1, le=100)


class TestRequest(BaseModel):
    source: str | None = None
    destination: str | None = None
    body: str = Field("netrescue-probe", max_length=4096)
    timeout_s: float = Field(5.0, gt=0.1, le=30.0)


class DetectorConfig(BaseModel):
    """Re-tune one detector. Omitted fields are left as they are."""

    link_id: str = Field(..., min_length=1, max_length=80)
    f_thresh: int | None = Field(None, ge=1, le=50)
    n: int | None = Field(None, ge=1, le=100)
    r_thresh: int | None = Field(None, ge=1, le=100)
    use_ewma: bool | None = None


class CompareRequest(BaseModel):
    link_id: str = Field("probe-link", min_length=1, max_length=80)
    #: True = heartbeat arrived, False = missed. Recorded input, replayed to
    #: both detectors so the comparison is like for like.
    sequence: list[bool] = Field(..., min_length=1, max_length=2000)
    f_thresh: int = Field(3, ge=1, le=50)
    n: int = Field(5, ge=1, le=100)
    r_thresh: int = Field(5, ge=1, le=100)
    use_ewma: bool = False
    baseline_threshold: int | None = Field(None, ge=1, le=100)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lab():
    lab = lab_service.lab
    if lab is None or not lab.running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No topology is running. Press “Apply to Lab” to start real "
                "forwarding nodes for the topology you drew."
            ),
        )
    return lab


def _audit(principal, action: str, resource: str, ok: bool, detail: dict) -> None:
    default_auditor().record(principal.name, action, resource, ok, detail)


def _failed_nodes(lab) -> set[str]:
    return {f.target for f in lab.failures if f.kind == "node"}


def _failed_links(lab) -> set[str]:
    return {f.target for f in lab.failures if f.kind == "link"}


def _current_route(lab, source: str, destination: str) -> list[str] | None:
    """
    The route the runtime would use right now.

    Read from the live router rather than recomputed here, so what the canvas
    highlights and what the forwarding tables hold cannot disagree.
    """
    node = lab.nodes.get(source)
    if node is None:
        return None
    result = node.router.best_route(
        source, destination, hop_limit=node.table.hop_limit
    )
    if result.reachable and len(result.path) > 1:
        return list(result.path)
    return None


def _route_links(path: list[str] | None) -> set[str]:
    if not path or len(path) < 2:
        return set()
    return {link_name(path[i], path[i + 1]) for i in range(len(path) - 1)}


def _endpoints() -> tuple[str | None, str | None]:
    """The pair the workspace is currently testing between."""
    topology = playground_service.topology
    source = playground_service.source or (
        topology.sources()[0] if topology.sources() else None
    )
    destination = playground_service.destination or (
        topology.destinations()[0] if topology.destinations() else None
    )
    return source, destination


def _link_issues(link, topology, running, failed_nodes, failed_links,
                 on_route, validation, detectors) -> tuple[list[dict], list[dict]]:
    """
    Why a specific link is or is not usable, in that link's own terms.

    `issues` are things that stop it carrying traffic; `notes` are facts about
    it. Nothing is called a problem for merely looking unusual — a link that
    simply is not on the current route is a note, not a fault.
    """
    issues: list[dict] = []
    notes: list[dict] = []

    missing = [e for e in (link.node_a, link.node_b)
               if e not in {n.node_id for n in topology.nodes}]
    if missing:
        issues.append({
            "code": "missing_endpoint",
            "severity": "failed",
            "message": (
                f"This link references {', '.join(missing)}, which is not a node in "
                "the topology. It cannot be forwarded over."
            ),
        })

    down = [e for e in (link.node_a, link.node_b) if e in failed_nodes]
    if down:
        issues.append({
            "code": "endpoint_down",
            "severity": "failed",
            "message": (
                f"{', '.join(down)} is FAILED — its listener is closed, so this "
                "link cannot carry traffic in either direction."
            ),
        })

    if link.link_id in failed_links:
        issues.append({
            "code": "link_down",
            "severity": "failed",
            "message": (
                "This link has been marked down. Every node's routing table was "
                "rebuilt without it, so no next hop crosses it."
            ),
        })

    if running and detectors:
        # Each end runs its own detector; either one seeing SUSPECT is a real
        # finding about this link, and which end it was matters to the reading.
        for endpoint, neighbour in ((link.node_a, link.node_b),
                                    (link.node_b, link.node_a)):
            reading = (detectors.get(endpoint) or {}).get(neighbour)
            if reading is not None and reading["state"] == "SUSPECT":
                issues.append({
                    "code": "endpoint_suspect",
                    "severity": "suspect",
                    "message": (
                        f"{endpoint}'s detector for this link reports SUSPECT after "
                        f"{reading['consecutive_misses']} consecutive missed "
                        f"heartbeats. It reaches FAILED at {reading['n']}."
                    ),
                })

    if not on_route and not issues:
        notes.append({
            "code": "off_route",
            "message": "Not part of the currently selected route.",
        })

    if validation and validation.get("has_alternate"):
        notes.append({
            "code": "has_alternate",
            "message": "An alternate route exists if this link fails.",
        })
    elif validation and not validation.get("has_alternate") and not on_route:
        notes.append({
            "code": "not_protected",
            "message": (
                "This link is not protected: failing it alone leaves the "
                "destination unreachable."
            ),
        })

    return issues, notes


def _snapshot() -> dict:
    """
    One payload the canvas, the panels and the timeline all read from.

    Assembled in a single place so the node states, the link states, the
    validation report and the route can never be pulled from different moments
    in time and disagree with each other.
    """
    topology = playground_service.topology
    lab = lab_service.lab
    running = bool(lab and lab.running)
    source, destination = _endpoints()

    failed_nodes = _failed_nodes(lab) if running else set()
    failed_links = _failed_links(lab) if running else set()

    validation = validate_topology(
        topology, source=source, destination=destination,
        failed_nodes=failed_nodes, failed_links=failed_links,
    )

    live_route = _current_route(lab, source, destination) if (
        running and source and destination
    ) else None
    on_route = _route_links(live_route)
    detectors = lab.detectors() if running else {}

    nodes_out: list[dict] = []
    for spec in topology.nodes:
        entry: dict[str, Any] = {
            **spec.as_dict(),
            "running": False,
            "failed": spec.node_id in failed_nodes,
            "port": None,
            "state": "DOWN",
            "detector": None,
            "routes": {},
            "counters": {},
        }
        if running and spec.node_id in lab.nodes:
            node = lab.nodes[spec.node_id]
            status_payload = node.status()
            entry.update({
                "running": status_payload["running"] and not entry["failed"],
                "port": node.port,
                "state": "DOWN" if entry["failed"] else "UP",
                "detector": node.detector_states(),
                "routes": status_payload["routes"],
                "counters": status_payload["counters"],
            })
        elif not running:
            entry["state"] = "IDLE"
        nodes_out.append(entry)

    links_out: list[dict] = []
    # One status pass for the whole snapshot. Building it per link would rebuild
    # every node's routing table view once per edge, and the states it returns
    # are the same for all of them.
    lab_status = lab.status() if running else None
    link_status = {l["link_id"]: l for l in lab_status["links"]} if lab_status else {}

    for spec in topology.links:
        link_state: dict[str, Any] = {
            **spec.as_dict(),
            "enabled": True,
            "status": "UP",
            "in_route": spec.link_id in on_route,
            "state_a": "UNKNOWN",
            "state_b": "UNKNOWN",
        }
        reported = link_status.get(spec.link_id)
        if reported:
            link_state.update({
                "status": reported["status"],
                "state_a": reported["state_a"],
                "state_b": reported["state_b"],
            })
        link_state["enabled"] = link_state["status"] == "UP" and not any(
            e in failed_nodes for e in (spec.node_a, spec.node_b)
        )
        issues, notes = _link_issues(
            spec, topology, running, failed_nodes, failed_links,
            spec.link_id in on_route, validation, detectors,
        )
        link_state["issues"] = issues
        link_state["notes"] = notes
        links_out.append(link_state)

    return {
        "topology": topology.as_dict(),
        "validation": validation,
        "running": running,
        "applied": playground_service.applied,
        "draft_is_running": running and _signature(topology) == playground_service.applied,
        "source": source,
        "destination": destination,
        "nodes": nodes_out,
        "links": links_out,
        "route": live_route,
        "previous_route": playground_service.previous_route,
        "route_changes": playground_service.route_changes,
        "packets": playground_service.packets,
        "last_delivery": playground_service.last_delivery,
        "detectors": detectors,
        "transitions": lab.transitions() if running else [],
        "events": playground_service.events[-200:],
        "experiments": playground_service.experiments[-20:],
        "max_nodes": MAX_NODES,
        "roles": list(VALID_ROLES),
    }


def _signature(topology: PlaygroundTopology) -> str:
    """A stable identity for a topology, so drift between draft and live is visible."""
    nodes = ",".join(sorted(topology.node_ids))
    links = ",".join(sorted(l.link_id for l in topology.links))
    return f"{nodes}|{links}"


def _note_route(before: list[str] | None, after: list[str] | None,
                source: str | None, destination: str | None) -> None:
    """
    Record a route change, if this one is a change.

    Compared against the route the last operation left in place, so a test packet
    that merely confirms the same path is not reported as a reroute.
    """
    playground_service.route = after
    if before == after:
        return
    if before is not None:
        playground_service.previous_route = before
        playground_service.route_changes += 1
        playground_service.record(
            "route",
            f"Route changed: {' → '.join(before) if before else 'none'} → "
            f"{' → '.join(after) if after else 'UNREACHABLE'}",
            "warning" if after is None else "info",
            {"previous": before, "current": after,
             "source": source, "destination": destination},
        )
    elif after is None:
        playground_service.record(
            "route",
            f"{source} cannot reach {destination}: no route available.",
            "warning", {"source": source, "destination": destination},
        )
    else:
        playground_service.record(
            "route",
            f"Route selected: {' → '.join(after)}", "info", {"current": after},
        )


def _to_topology(body: TopologyInput) -> PlaygroundTopology:
    return PlaygroundTopology(
        nodes=[
            TopologyNodeSpec(n.node_id, n.label or n.node_id, n.role, n.x, n.y)
            for n in body.nodes
        ],
        links=[
            TopologyLinkSpec(
                f"{min(l.node_a, l.node_b)}-{max(l.node_a, l.node_b)}",
                l.node_a, l.node_b,
            )
            for l in body.links
        ],
    )


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@router.get("/state")
def get_state(_: RequireRead):
    """
    Everything the workbench renders, in one consistent read.

    Always 200. An empty workspace is a normal state for a screen that renders
    an empty view, not an error to be handled specially.
    """
    return _snapshot()


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


@router.put("/topology")
def save_topology(body: TopologyInput, principal: RequireOperator):
    """
    Save the topology that was drawn, and validate it in the same request.

    The save is unconditional: an invalid graph is still what the operator drew,
    and throwing it away would leave the canvas disagreeing with the server. What
    changes is that it is *not applied* until it validates — see /apply.
    """
    playground_service.topology = _to_topology(body)
    playground_service.source = body.source or playground_service.source
    playground_service.destination = body.destination or playground_service.destination
    playground_service.record(
        "edit", "Topology saved: "
                f"{len(body.nodes)} nodes, {len(body.links)} links.", "info",
        {"nodes": len(body.nodes), "links": len(body.links)},
    )
    _audit(principal, "playground.topology.save", "draft", True,
           {"nodes": len(body.nodes), "links": len(body.links)})
    return _snapshot()


@router.post("/topology/example")
def load_example(principal: RequireOperator):
    """Load the starting example. A starting point, not a requirement."""
    playground_service.topology = example_topology()
    playground_service.source = None
    playground_service.destination = None
    playground_service.record("edit", "Example network loaded.", "info")
    _audit(principal, "playground.topology.example", "draft", True, {})
    return _snapshot()


@router.post("/topology/clear")
async def clear_topology(principal: RequireOperator):
    """
    Empty the workspace.

    Stops any running network first, and awaits it: the sockets have to be
    released before the topology they were serving is thrown away, or a packet
    could still cross a wire the operator has just deleted from the canvas.
    """
    await _stop_lab()
    playground_service.topology = PlaygroundTopology()
    playground_service.source = None
    playground_service.destination = None
    playground_service.applied = None
    playground_service.route = None
    playground_service.previous_route = None
    playground_service.record("edit", "Workspace cleared.", "info")
    _audit(principal, "playground.topology.clear", "draft", True, {})
    return _snapshot()


@router.post("/validate")
def validate_only(body: ValidateRequest, principal: RequireOperator):
    """
    Check the topology without starting anything.

    Answers the two questions separately: is this a network, and can it carry a
    packet. A graph with no source-to-destination path is structurally valid and
    still useless, and saying only “invalid” would hide which of the two is wrong.
    """
    topology = playground_service.topology
    lab = lab_service.lab
    running = bool(lab and lab.running)
    report = validate_topology(
        topology,
        source=body.source or playground_service.source,
        destination=body.destination or playground_service.destination,
        failed_nodes=_failed_nodes(lab) if running else set(),
        failed_links=_failed_links(lab) if running else set(),
    )
    if report["valid"] and report["routable"]:
        severity = "info"
        if report.get("has_alternate"):
            message = (
                f"Valid. {report['source']} can reach {report['destination']} by "
                f"{report['path_count']} paths, so a failure can be routed around."
            )
        else:
            message = (
                f"Structurally valid, and {report['source']} reaches "
                f"{report['destination']} — but by exactly one path. Failing "
                "anything on it makes the destination unreachable."
            )
    elif report["valid"]:
        severity = "warning"
        message = report["summary"]
    else:
        severity = "error"
        message = report["summary"]

    playground_service.record("validate", f"Validation: {message}", severity, report)
    _audit(principal, "playground.topology.validate", "draft", report["valid"],
           {"routable": report["routable"], "errors": len(report["errors"])})
    return {**_snapshot(), "message": message}


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


async def _stop_lab() -> None:
    if lab_service.lab is not None and lab_service.lab.running:
        await lab_service.lab.stop()


@router.post("/apply")
async def apply_topology(body: ApplyRequest, principal: RequireOperator):
    """
    Start real forwarding nodes for the topology on the canvas.

    One TCP listener per node on its own loopback port, wired exactly as drawn,
    with heartbeats and a hysteresis detector per link. This is the point where
    the drawing becomes a network — nothing before this point has forwarded
    anything, and nothing after it can claim a delivery the runtime did not make.

    A topology with no source-to-destination path is *not* refused. It is well
    formed, and building a chain one link at a time is legitimate work; the
    nodes start, the gap is reported as a gap, and any packet sent across it
    comes back not delivered with the reason. Only a malformed topology — a
    duplicate id, a link to nothing — is rejected here, because that one cannot
    be started at all.
    """
    topology = playground_service.topology
    report = validate_topology(topology)
    if not report["valid"]:
        playground_service.record(
            "lifecycle", f"Apply refused: {report['summary']}", "error", report,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": report["summary"], "validation": report},
        )

    await lab_service.start()
    lab = lab_service.lab
    assert lab is not None

    try:
        await lab.start(
            topology.node_ids,
            edges=topology.edges(),
            heartbeat_interval_s=body.heartbeat_interval_s,
            f_thresh=body.f_thresh,
            n=body.n,
            r_thresh=body.r_thresh,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    source, destination = _endpoints()
    playground_service.applied = _signature(topology)
    playground_service.route = None
    playground_service.previous_route = None
    playground_service.route_changes = 0
    playground_service.packets = {"sent": 0, "delivered": 0, "failed": 0}
    playground_service.last_delivery = None

    route = _current_route(lab, source, destination) if source and destination else None
    _note_route(None, route, source, destination)
    playground_service.record(
        "lifecycle",
        f"Applied: {len(topology.nodes)} forwarding nodes started on real sockets.",
        "info",
        {"nodes": topology.node_ids, "edges": topology.edges(),
         "heartbeat_interval_s": body.heartbeat_interval_s,
         "f_thresh": body.f_thresh, "n": body.n, "r_thresh": body.r_thresh},
    )
    if not report["routable"]:
        playground_service.record(
            "lifecycle",
            f"Applied with no usable route: {report['summary']}",
            "warning", report,
        )
    _audit(principal, "playground.topology.apply", "lab", True,
           {"nodes": topology.node_ids, "links": len(topology.links),
            "routable": report["routable"]})
    return execution.attach(dict(_snapshot()), "overlay.live",
                            target="127.0.0.1 (one loopback TCP listener per node)",
                            inputs={"nodes": topology.node_ids, "edges": topology.edges()},
                            outcome="listeners started")


@router.post("/teardown")
async def teardown(principal: RequireOperator):
    """Stop every forwarding node and release its sockets."""
    if lab_service.lab is None or not lab_service.lab.running:
        return {"message": "No topology was running.", **_snapshot()}
    await _stop_lab()
    playground_service.applied = None
    playground_service.route = None
    playground_service.record("lifecycle", "Forwarding nodes stopped.", "info")
    _audit(principal, "playground.topology.teardown", "lab", True, {})
    return _snapshot()


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------


@router.post("/test")
async def run_test(body: TestRequest, principal: RequireOperator):
    """
    Send one packet and report what the destination said.

    `delivered` is true only when the destination node acknowledged the message.
    There is no path through this endpoint that returns success for a packet the
    runtime did not see arrive.
    """
    lab = _lab()
    source, destination = body.source or playground_service.source, \
        body.destination or playground_service.destination
    source = source or (playground_service.topology.sources() or [None])[0]
    destination = destination or (
        playground_service.topology.destinations() or [None]
    )[0]

    if not source or not destination:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Choose a source and a destination before running a test.",
        )
    if source == destination:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Source and destination are both {source!r} — pick two nodes.",
        )

    before = _current_route(lab, source, destination)
    playground_service.record(
        "test", f"Connectivity test started: {source} → {destination}.", "info",
        {"source": source, "destination": destination},
    )

    try:
        result = await lab.send(
            source, destination,
            body=body.body.encode("utf-8", errors="replace"),
            timeout=body.timeout_s,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{exc} Known nodes: {sorted(lab.nodes)}",
        ) from exc

    after = _current_route(lab, source, destination)
    _note_route(before, after, source, destination)

    playground_service.packets["sent"] += 1
    if result.get("delivered"):
        playground_service.packets["delivered"] += 1
    else:
        playground_service.packets["failed"] += 1

    enriched = {
        **result,
        "source": source,
        "destination": destination,
        "route_after": after,
        "route_version": lab.nodes[source].router.current_version,
        "link_counters": {
            nid: lab.nodes[nid].status()["counters"] for nid in lab.nodes
        },
    }
    playground_service.last_delivery = enriched
    playground_service.record(
        "delivery" if result.get("delivered") else "delivery",
        (
            f"Packet delivered: {source} → {destination} via "
            f"{' → '.join(result['path'])}"
            if result.get("delivered") else
            f"Delivery failed: {source} → {destination} — {result.get('reason')}"
        ),
        "info" if result.get("delivered") else "error",
        enriched,
    )

    _audit(principal, "playground.test", f"{source}->{destination}",
           bool(result.get("delivered")),
           {"delivered": bool(result.get("delivered")),
            "path": result.get("path"), "reason": result.get("reason")})
    return execution.attach(
        dict(enriched), "overlay.live", target=f"{source} -> {destination} over loopback sockets",
        outcome=("delivered (destination acknowledged)" if result.get("delivered")
                 else f"not delivered: {result.get('reason')}"))


# ---------------------------------------------------------------------------
# Faults
# ---------------------------------------------------------------------------


@router.post("/nodes/{node_id}/fail")
async def fail_node(node_id: str, principal: RequireOperator):
    """
    Take a node down for real.

    Its listener is closed, so it genuinely cannot forward. Peers then find an
    alternate route where one exists and report UNREACHABLE where none does —
    neither outcome is decided here, it is read back off the routing tables.
    """
    lab = _lab()
    source, destination = _endpoints()
    before = _current_route(lab, source, destination)

    try:
        await lab.fail_node(node_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{exc} Known nodes: {sorted(lab.nodes)}",
        ) from exc

    after = _current_route(lab, source, destination)
    _note_route(before, after, source, destination)
    playground_service.record(
        "failure", f"Node {node_id} failed — listener closed.", "warning",
        {"node_id": node_id, "route": after},
    )
    _audit(principal, "playground.node.fail", node_id, True, {"node_id": node_id})
    return _snapshot()


@router.post("/nodes/{node_id}/restore")
async def restore_node(node_id: str, principal: RequireOperator):
    """Bring a failed node back and rejoin it to the topology."""
    lab = _lab()
    source, destination = _endpoints()
    before = _current_route(lab, source, destination)

    try:
        await lab.restore_node(node_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{exc} Known nodes: {sorted(lab.nodes)}",
        ) from exc

    after = _current_route(lab, source, destination)
    _note_route(before, after, source, destination)
    playground_service.record(
        "restore", f"Node {node_id} restored — accepting traffic again.", "info",
        {"node_id": node_id, "route": after},
    )
    _audit(principal, "playground.node.restore", node_id, True, {"node_id": node_id})
    return _snapshot()


@router.post("/links/{link_id:path}/fail")
async def fail_link(link_id: str, principal: RequireOperator):
    """
    Mark a link down everywhere.

    Every node's router is updated and every forwarding table rebuilt, so no
    next hop still crosses the failed link and the delivery test that follows is
    a real test rather than a red line on a canvas.
    """
    lab = _lab()
    source, destination = _endpoints()
    before = _current_route(lab, source, destination)

    try:
        await lab.fail_link(link_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{exc} Known links: {sorted(lab._links)}",
        ) from exc

    after = _current_route(lab, source, destination)
    canonical = link_name(*lab.resolve_link(link_id))
    _note_route(before, after, source, destination)
    playground_service.record(
        "failure", f"Link {canonical} disabled.", "warning",
        {"link_id": canonical, "route": after},
    )
    _audit(principal, "playground.link.fail", canonical, True,
           {"link_id": canonical})
    return _snapshot()


@router.post("/links/{link_id:path}/restore")
async def restore_link(link_id: str, principal: RequireOperator):
    """Bring a link back up and let the routing tables use it again."""
    lab = _lab()
    source, destination = _endpoints()
    before = _current_route(lab, source, destination)

    try:
        await lab.restore_link(link_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{exc} Known links: {sorted(lab._links)}",
        ) from exc

    canonical = link_name(*lab.resolve_link(link_id))
    after = _current_route(lab, source, destination)
    _note_route(before, after, source, destination)
    playground_service.record(
        "restore", f"Link {canonical} restored.", "info",
        {"link_id": canonical, "route": after},
    )
    _audit(principal, "playground.link.restore", canonical, True,
           {"link_id": canonical})
    return _snapshot()


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


@router.get("/detectors")
def get_detectors(_: RequireRead):
    """
    What each link's detector currently reads, and what it is configured to do.

    Both halves matter. The thresholds are what the operator set; the state and
    the consecutive-miss counters are what the node actually observed on real
    heartbeats. Reporting only the first would let a claim of detection be a
    claim about configuration.
    """
    lab = _lab()
    return {
        "detectors": lab.detectors(),
        "transitions": lab.transitions(),
        "states_implemented": ["HEALTHY", "SUSPECT", "FAILED"],
        "note": (
            "HEALTHY → SUSPECT → FAILED → HEALTHY. Recovery is a direct "
            "FAILED → HEALTHY transition once R_thresh consecutive heartbeats "
            "arrive; there is no separate RECOVERING state in this detector."
        ),
    }


@router.post("/detectors")
def configure_detector(body: DetectorConfig, principal: RequireOperator):
    """
    Re-tune one link's detector, live, on both of its endpoints.

    Both ends hold a detector because each measures the heartbeats it sends and
    receives itself. Retuning only one would leave two nodes disagreeing about
    the same wire.
    """
    lab = _lab()
    try:
        a, b = lab.resolve_link(body.link_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{exc} Known links: {sorted(lab._links)}",
        ) from exc

    params = {
        k: v for k, v in (
            ("f_thresh", body.f_thresh),
            ("n", body.n),
            ("r_thresh", body.r_thresh),
            ("use_ewma", body.use_ewma),
        ) if v is not None
    }
    if not params:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Supply at least one of f_thresh, n, r_thresh or use_ewma.",
        )

    configured = []
    for node_id, neighbour in ((a, b), (b, a)):
        try:
            state = lab.configure_detector(node_id, neighbour, **params)
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        configured.append({"node_id": node_id, **state})

    ratio = configured[0]["hysteresis_ratio"] if configured else None
    playground_service.record(
        "detector",
        f"Detector for {body.link_id} retuned: "
        + ", ".join(f"{k}={v}" for k, v in params.items())
        + f" (hysteresis_ratio {ratio}).",
        "warning" if ratio is not None and ratio <= 1.0 else "info",
        {"link_id": body.link_id, "params": params, "hysteresis_ratio": ratio},
    )
    _audit(principal, "playground.detector.configure", body.link_id, True,
           {"params": params})
    return {"detectors": configured, "hysteresis_ratio": ratio,
            "snapshot": _snapshot()}


@router.get("/transitions")
def get_transitions(_: RequireRead, link_id: str | None = None):
    """
    Detector transitions the running nodes have actually made.

    Read from the nodes' own record rather than from a log written alongside
    them, so it cannot claim a transition that did not move a detector.
    """
    lab = _lab()
    transitions = lab.transitions(link_id)
    return {
        "link_id": link_id,
        "transitions": transitions,
        "count": len(transitions),
        "states_implemented": ["HEALTHY", "SUSPECT", "FAILED"],
    }


@router.post("/hysteresis/compare")
def compare_detectors(body: CompareRequest, principal: RequireOperator):
    """
    Replay one recorded heartbeat sequence through both detectors.

    The same input, in the same order, to a three-state machine with a dead band
    and to a single-threshold machine without one. What comes back is what each
    detector did with it — transitions and route-flap counts, measured rather
    than asserted — so a reader can check the comparison instead of taking it on
    trust.

    This is a replay of recorded detector behaviour, not a live network
    experiment: it changes no link and moves no packet. Use /detectors and a
    real failure for that.
    """
    try:
        report = run_detector_comparison(
            link_id=body.link_id,
            sequence=body.sequence,
            f_thresh=body.f_thresh,
            n=body.n,
            r_thresh=body.r_thresh,
            use_ewma=body.use_ewma,
            baseline_threshold=body.baseline_threshold,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    playground_service.experiments.append(report)
    del playground_service.experiments[:-50]
    saved = report["route_flaps_saved"]
    playground_service.record(
        "hysteresis",
        f"Hysteresis vs single-threshold over {len(body.sequence)} heartbeats: "
        f"{report['hysteresis']['route_flap_count']} route changes vs "
        f"{report['naive']['route_flap_count']} "
        f"({saved:+d} for hysteresis).",
        "info", report,
    )
    _audit(principal, "playground.hysteresis.compare", body.link_id, True,
           {"samples": len(body.sequence), "route_flaps_saved": saved})
    return execution.attach(dict(report), "overlay.detector_comparison",
                            inputs={"samples": len(body.sequence), "f_thresh": body.f_thresh,
                                    "n": body.n, "r_thresh": body.r_thresh,
                                    "use_ewma": body.use_ewma})


# ---------------------------------------------------------------------------
# Journal and export
# ---------------------------------------------------------------------------


@router.get("/events")
def get_events(_: RequireRead, limit: int = 500):
    """The experiment journal — every entry the backend actually produced."""
    return {
        "events": playground_service.events[-min(max(limit, 1), 1000):],
        "count": len(playground_service.events),
    }


@router.post("/events/clear")
def clear_events(principal: RequireOperator):
    """Empty the journal. The network keeps running; only the record is dropped."""
    playground_service.clear_events()
    _audit(principal, "playground.events.clear", "journal", True, {})
    return {"message": "Event timeline cleared.", **_snapshot()}


@router.get("/export")
def export(
    _: RequireRead,
    fmt: Literal["json", "csv"] = "json",
):
    """
    The experiment log, as a download.

    Contains the journal, the topology, the detector configuration and the
    packet counts exactly as the backend recorded them. `csv` is the journal
    alone, flattened — one row per event — for a spreadsheet; `json` is the
    whole experiment, for re-analysis.
    """
    topology = playground_service.topology.as_dict()
    lab = lab_service.lab
    running = bool(lab and lab.running)
    document = {
        "generated_at": time.time(),
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "topology": topology,
        "source": playground_service.source,
        "destination": playground_service.destination,
        "running": running,
        "route": playground_service.route,
        "previous_route": playground_service.previous_route,
        "route_changes": playground_service.route_changes,
        "packets": playground_service.packets,
        "last_delivery": playground_service.last_delivery,
        "detectors": lab.detectors() if running else {},
        "transitions": lab.transitions() if running else [],
        "validation": validate_topology(
            topology_as_spec(topology),
            source=playground_service.source,
            destination=playground_service.destination,
            failed_nodes=_failed_nodes(lab) if running else set(),
            failed_links=_failed_links(lab) if running else set(),
        ),
        "experiments": playground_service.experiments,
        "events": playground_service.events,
    }

    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["seq", "timestamp", "iso_time", "category",
                         "severity", "message", "detail"])
        for event in playground_service.events:
            writer.writerow([
                event["seq"],
                f"{event['ts']:.6f}",
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(event["ts"])),
                event["category"],
                event["severity"],
                event["message"],
                event.get("detail", {}),
            ])
        return {
            "filename": "netrescue-playground-events.csv",
            "content": buffer.getvalue(),
            "rows": len(playground_service.events),
        }

    return {
        "filename": "netrescue-playground-experiment.json",
        "content": json.dumps(document, indent=2, default=str),
        "events": len(playground_service.events),
    }


def topology_as_spec(payload: dict) -> PlaygroundTopology:
    """Rebuild a PlaygroundTopology from its serialised form."""
    return PlaygroundTopology(
        nodes=[
            TopologyNodeSpec(n["node_id"], n.get("label", ""),
                             n.get("role", "router"),
                             float(n.get("x", 0.0)), float(n.get("y", 0.0)))
            for n in payload["nodes"]
        ],
        links=[
            TopologyLinkSpec(l["link_id"], l["node_a"], l["node_b"])
            for l in payload["links"]
        ],
    )