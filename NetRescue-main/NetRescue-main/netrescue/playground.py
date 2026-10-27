"""
netrescue/playground.py — topology authored in a browser, checked before it runs.

The Recovery Lab starts a real forwarding network: one OS-level listener per
node, real sockets between them, real heartbeats. That is what makes a delivery
result mean something, and it is also why the shape of the network cannot be
decided by the front end and merely drawn. Whatever the operator draws has to
be checkable here, on the server, before any socket is opened — otherwise the
canvas and the running network drift apart and a green tick on screen means
nothing.

Two ideas carry the module:

**Structural validity is not routability.** A graph can be perfectly well
formed — unique ids, every link pointing at a node that exists — and still have
no path from the source to the destination. Those are different findings and
they are reported separately, because conflating them produces the worst kind of
wrong answer: a network that looks fine and cannot carry a packet.

**Hysteresis has to be measured against something.** `run_detector_comparison`
drives the three-state machine and the naïve single-threshold detector with the
*same* recorded heartbeat sequence and reports the transitions each one actually
produced. Any difference in route changes is then a property of the detectors,
not of two different experiments.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import networkx as nx

from netrescue.lab import MAX_NODES
from netrescue.recovery.naive import NaiveThresholdDetector
from netrescue.recovery.state_machine import HysteresisStateMachine

log = logging.getLogger(__name__)

#: Node ids are operator-visible in every link id, every route and every event,
#: so they are held to a shape that stays readable in all three. "-" is excluded
#: because it separates the endpoints of a link id (`A-B`), and an id containing
#: it would make `A-1-B` genuinely ambiguous to a human reading the canvas.
NODE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,32}$")

VALID_ROLES = ("source", "router", "destination")

#: Cap on the paths enumerated for a topology. A hand-built graph of at most
#: MAX_NODES nodes cannot produce more than a few hundred in practice; the cap
#: exists so a pathological shape cannot turn validation into a long request.
MAX_ENUMERATED_PATHS = 200


# ---------------------------------------------------------------------------
# Topology model
# ---------------------------------------------------------------------------


@dataclass
class TopologyNodeSpec:
    """One node as the operator drew it."""

    node_id: str
    label: str = ""
    role: str = "router"
    x: float = 0.0
    y: float = 0.0

    def as_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "label": self.label or self.node_id,
            "role": self.role,
            "x": self.x,
            "y": self.y,
        }


@dataclass
class TopologyLinkSpec:
    """One connection as the operator drew it."""

    link_id: str
    node_a: str
    node_b: str

    def as_dict(self) -> dict:
        return {
            "link_id": self.link_id,
            "node_a": self.node_a,
            "node_b": self.node_b,
        }


@dataclass
class PlaygroundTopology:
    """A user-authored network: nodes with roles, and the connections between."""

    nodes: list[TopologyNodeSpec] = field(default_factory=list)
    links: list[TopologyLinkSpec] = field(default_factory=list)

    # ------------------------------------------------------------------

    @property
    def node_ids(self) -> list[str]:
        return [n.node_id for n in self.nodes]

    def link_id_for(self, a: str, b: str) -> str:
        return f"{min(a, b)}-{max(a, b)}"

    def find_link(self, a: str, b: str) -> TopologyLinkSpec | None:
        key = (min(a, b), max(a, b))
        for link in self.links:
            if (min(link.node_a, link.node_b), max(link.node_a, link.node_b)) == key:
                return link
        return None

    def node(self, node_id: str) -> TopologyNodeSpec | None:
        return next((n for n in self.nodes if n.node_id == node_id), None)

    def sources(self) -> list[str]:
        return [n.node_id for n in self.nodes if n.role == "source"]

    def destinations(self) -> list[str]:
        return [n.node_id for n in self.nodes if n.role == "destination"]

    def edges(self) -> list[tuple[str, str]]:
        return [(l.node_a, l.node_b) for l in self.links]

    def as_dict(self) -> dict:
        return {
            "nodes": [n.as_dict() for n in self.nodes],
            "links": [l.as_dict() for l in self.links],
        }


def example_topology() -> PlaygroundTopology:
    """
    The starting example: one source, three forwarders, one destination, and two
    independent ways between source and destination.

    A is a starting point, not a constraint — it is loaded by an explicit button
    and is overwritable field by field.
    """
    return PlaygroundTopology(
        nodes=[
            TopologyNodeSpec("A", "Source A", "source", 90, 200),
            TopologyNodeSpec("B", "Router B", "router", 320, 90),
            TopologyNodeSpec("C", "Router C", "router", 320, 310),
            TopologyNodeSpec("D", "Router D", "router", 610, 200),
            TopologyNodeSpec("E", "Destination E", "destination", 880, 200),
        ],
        links=[
            TopologyLinkSpec("A-B", "A", "B"),
            TopologyLinkSpec("A-C", "A", "C"),
            TopologyLinkSpec("B-D", "B", "D"),
            TopologyLinkSpec("C-D", "C", "D"),
            TopologyLinkSpec("D-E", "D", "E"),
        ],
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _issue(code: str, message: str, **extra: Any) -> dict:
    return {"code": code, "message": message, **extra}


def validate_topology(
    topology: PlaygroundTopology,
    source: str | None = None,
    destination: str | None = None,
    failed_nodes: Iterable[str] = (),
    failed_links: Iterable[str] = (),
) -> dict:
    """
    Check a topology and report what it can and cannot do.

    Returns a report with two independent verdicts:

      * ``valid``     — the topology is well formed (unique, resolvable, wired)
      * ``routable``  — a source can reach a destination through it right now

    and the paths themselves, so the caller can show the selected route and the
    alternate one rather than asserting that one exists. `failed_nodes` and
    `failed_links` narrow the analysis to the network as it currently stands,
    which is what makes "is there anything left after I cut this?" answerable.
    """
    errors: list[dict] = []
    warnings: list[dict] = []

    ids = [n.node_id for n in topology.nodes]

    # -- node ids ---------------------------------------------------------
    seen: set[str] = set()
    for node in topology.nodes:
        node_id = node.node_id
        if not NODE_ID_PATTERN.match(node_id):
            errors.append(_issue(
                "invalid_node_id",
                f"Node id {node_id!r} is not usable. Use 1-32 characters from "
                "A-Z, a-z, 0-9 and _ (a '-' would be ambiguous in link ids).",
                target=node_id,
            ))
        if node_id in seen:
            errors.append(_issue(
                "duplicate_node_id",
                f"Node id {node_id!r} is used more than once. Ids must be unique.",
                target=node_id,
            ))
        seen.add(node_id)
        if node.role not in VALID_ROLES:
            errors.append(_issue(
                "invalid_role",
                f"Node {node_id!r} has role {node.role!r}; expected one of "
                f"{list(VALID_ROLES)}.",
                target=node_id,
            ))

    if len(ids) > MAX_NODES:
        errors.append(_issue(
            "too_many_nodes",
            f"{len(ids)} nodes exceeds the supported maximum of {MAX_NODES}.",
        ))

    # -- links ------------------------------------------------------------
    known = set(ids)
    wired: set[tuple[str, str]] = set()
    for link in topology.links:
        a, b = link.node_a, link.node_b
        if a == b:
            errors.append(_issue(
                "self_link",
                f"Link {link.link_id!r} connects {a!r} to itself.",
                target=link.link_id,
            ))
        for endpoint, side in ((a, "node_a"), (b, "node_b")):
            if endpoint not in known:
                errors.append(_issue(
                    "dangling_link",
                    f"Link {link.link_id!r} references {endpoint!r} on {side}, "
                    f"which is not a node in this topology.",
                    target=link.link_id,
                    endpoint=endpoint,
                ))
        key = (min(a, b), max(a, b))
        if key in wired:
            errors.append(_issue(
                "duplicate_link",
                f"{key[0]} and {key[1]} are connected more than once.",
                target=link.link_id,
            ))
        wired.add(key)

    # -- roles ------------------------------------------------------------
    sources = topology.sources()
    destinations = topology.destinations()
    if not sources:
        errors.append(_issue("no_source", "The topology has no node with the source role."))
    if not destinations:
        errors.append(_issue(
            "no_destination",
            "The topology has no node with the destination role.",
        ))

    src = source if source is not None else (sources[0] if sources else None)
    dst = destination if destination is not None else (destinations[0] if destinations else None)

    if src is not None and src not in known:
        errors.append(_issue("unknown_source", f"Source {src!r} is not a node.", target=src))
        src = None
    if dst is not None and dst not in known:
        errors.append(_issue(
            "unknown_destination", f"Destination {dst!r} is not a node.", target=dst,
        ))
        dst = None
    if src is not None and dst is not None and src == dst:
        errors.append(_issue(
            "same_endpoint",
            f"Source and destination are both {src!r}. Pick two different nodes.",
            target=src,
        ))

    # -- routing ----------------------------------------------------------
    graph = nx.Graph()
    graph.add_nodes_from(known)
    for link in topology.links:
        if link.node_a in known and link.node_b in known and link.node_a != link.node_b:
            graph.add_edge(link.node_a, link.node_b, link_id=link.link_id)

    connected: list[str] = []
    if len(graph):
        largest = max(nx.connected_components(graph), key=len)
        connected = sorted(largest)
        for node_id in sorted(known - set(largest)):
            warnings.append(_issue(
                "isolated_node",
                f"Node {node_id!r} is not connected to any other node.",
                target=node_id,
            ))

    failed_node_set = {n for n in failed_nodes}
    failed_link_set = set(failed_links)

    def path_count(g: nx.Graph) -> int:
        if src is None or dst is None or src not in g or dst not in g:
            return 0
        try:
            paths = nx.all_simple_paths(g, src, dst)
            total = 0
            for _ in paths:
                total += 1
                if total >= MAX_ENUMERATED_PATHS:
                    break
            return total
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return 0

    paths: list[list[str]] = []
    if src is not None and dst is not None and src in graph and dst in graph:
        try:
            for path in nx.all_simple_paths(graph, src, dst):
                paths.append(path)
                if len(paths) >= MAX_ENUMERATED_PATHS:
                    warnings.append(_issue(
                        "path_enumeration_truncated",
                        f"More than {MAX_ENUMERATED_PATHS} paths exist; the route "
                        "list is truncated.",
                    ))
                    break
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            paths = []

    primary = paths[0] if paths else None
    alternate = paths[1] if len(paths) > 1 else None

    # The same question asked of the network as it stands, failures applied.
    live = graph.copy()
    for node_id in failed_node_set:
        if live.has_node(node_id):
            live.remove_node(node_id)
    for u, v, data in list(graph.edges(data=True)):
        if data.get("link_id") in failed_link_set and live.has_edge(u, v):
            live.remove_edge(u, v)

    live_count = path_count(live)
    live_primary = None
    if src is not None and dst is not None and src in live and dst in live:
        try:
            live_primary = next(nx.all_simple_paths(live, src, dst), None)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            live_primary = None

    routable = primary is not None
    if routable and not live_primary:
        errors.append(_issue(
            "destination_unreachable",
            f"{src} cannot reach {dst} with the current failures applied. "
            + (
                f"Cutting {' , '.join(sorted(failed_link_set))} removed the only "
                f"remaining path."
                if failed_link_set else
                f"Failing {' , '.join(sorted(failed_node_set))} removed the only "
                "remaining path."
                if failed_node_set else
                "No path remains in the current network state."
            ),
            target=dst,
        ))

    report = {
        "valid": not errors,
        "routable": routable,
        "source": src,
        "destination": dst,
        "primary_path": primary,
        "alternate_path": alternate,
        "path_count": len(paths),
        "has_alternate": alternate is not None,
        "live_path": live_primary,
        "live_path_count": live_count,
        "connected_nodes": connected,
        "errors": errors,
        "warnings": warnings,
        "summary": _summarise(src, dst, routable, alternate, errors),
    }
    return report


def _summarise(src, dst, routable, alternate, errors) -> str:
    if errors:
        return errors[0]["message"]
    if not routable:
        return (
            f"The topology is structurally valid but has no path from "
            f"{src} to {dst}, so no packet can be delivered."
        )
    if alternate is None:
        return (
            f"The topology is valid and {src} reaches {dst}, but by exactly one "
            "path. Break anything on it and the destination becomes unreachable."
        )
    return (
        f"{src} reaches {dst} by {alternate and 'several paths'}. "
        "Failures on the current route can be routed around."
    )


# ---------------------------------------------------------------------------
# Hysteresis experiment
# ---------------------------------------------------------------------------


def run_detector_comparison(
    link_id: str,
    sequence: list[bool],
    f_thresh: int = 3,
    n: int = 5,
    r_thresh: int = 5,
    use_ewma: bool = False,
    baseline_threshold: int | None = None,
    run_id: str | None = None,
    on_step: Callable[[dict], None] | None = None,
) -> dict:
    """
    Drive the hysteresis detector and the naïve one over the same heartbeats.

    `sequence` is a list of heartbeat outcomes: True means the beat arrived,
    False means it was missed. Both detectors are stepped once per entry, in the
    same order, so any difference in the transitions they report is a property
    of the detector rather than of the input.

    Reported, not estimated: the transition list, the number of route flaps (each
    defined the same way in both machines — a change in the usable forwarding
    path — so the counts are comparable), and the resulting state. Nothing here
    is a percentage improvement; the caller can compute one from these numbers
    or not at all.
    """
    if not sequence:
        raise ValueError("a heartbeat sequence of at least one entry is required")

    # f_thresh defaults to the baseline threshold when the caller asks for a fair
    # comparison: the naive machine is the one-threshold version of the same
    # detector, so it should trip where the hysteresis machine first suspects.
    threshold = baseline_threshold if baseline_threshold is not None else f_thresh

    hysteresis_transitions: list[dict] = []
    naive_transitions: list[dict] = []

    machine = HysteresisStateMachine(
        link_id=link_id,
        f_thresh=f_thresh,
        n=n,
        r_thresh=r_thresh,
        use_ewma=use_ewma,
        run_id=run_id,
        on_transition=lambda evt: hysteresis_transitions.append({
            "from": evt.from_state,
            "to": evt.to_state,
            "route_flap": evt.route_flap,
        }),
    )
    naive = NaiveThresholdDetector(
        link_id=link_id,
        threshold=threshold,
        run_id=run_id,
        on_transition=lambda evt: naive_transitions.append({
            "from": evt.from_state,
            "to": evt.to_state,
            "route_flap": evt.route_flap,
        }),
    )

    timeline: list[dict] = []
    for index, arrived in enumerate(sequence):
        before = (machine.state.value, naive.state)
        machine.update(missed=not arrived)
        naive.update(missed=not arrived)
        step = {
            "index": index,
            "heartbeat": "arrived" if arrived else "missed",
            "hysteresis_state": machine.state.value,
            "naive_state": naive.state,
            "hysteresis_flaps": machine.route_flap_count,
            "naive_flaps": naive.route_flap_count,
            "changed": (before[0], before[1]) != (machine.state.value, naive.state),
        }
        timeline.append(step)
        if on_step is not None:
            on_step(step)

    hysteresis_flaps = machine.route_flap_count
    naive_flaps = naive.route_flap_count
    saved = hysteresis_flaps - naive_flaps if naive_flaps else 0

    return {
        "link_id": link_id,
        "run_id": run_id,
        "sequence": ["arrived" if s else "missed" for s in sequence],
        "hysteresis": {
            "detector": "HysteresisStateMachine",
            "f_thresh": f_thresh,
            "n": n,
            "r_thresh": r_thresh,
            "hysteresis_ratio": round(machine.hysteresis_ratio, 3),
            "use_ewma": use_ewma,
            "transitions": hysteresis_transitions,
            "route_flap_count": hysteresis_flaps,
            "final_state": machine.state.value,
        },
        "naive": {
            "detector": "NaiveThresholdDetector",
            "threshold": threshold,
            "transitions": naive_transitions,
            "route_flap_count": naive_flaps,
            "final_state": naive.state,
        },
        "route_flaps_saved": saved,
        "timeline": timeline,
        "note": (
            "Both detectors consumed the identical heartbeat sequence. A route "
            "flap is a change in the usable forwarding path, counted identically "
            "in both machines; SUSPECT does not flap in the hysteresis machine."
        ),
    }