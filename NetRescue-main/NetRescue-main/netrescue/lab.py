"""
netrescue/lab.py — a live overlay topology an operator can actually break.

Holds real ForwardingNodes on real sockets and exposes the operations the
Recovery Lab needs: start a topology, fail a node or a link, restore it, send
data, and observe routes.

Two rules shape everything here:

1. *Recovery is never reported before it is verified.* `send()` returns True only
   when the destination node acknowledges. Route tables changing is not evidence
   of recovery; a delivered message is.
2. *Failures are real.* `fail_node()` stops the node's listener, so the failure
   is a genuine inability to forward rather than a flag that routes around.

Everything here is in-process and in-memory. It is a lab harness, not a daemon:
the API owns one instance for the lifetime of the server.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from netrescue.forwarding.node import ForwardEntry, ForwardingNode, link_name
from netrescue.security.hmac_auth import generate_key

log = logging.getLogger(__name__)

#: Hard cap so a hostile or mistaken request cannot exhaust the host.
MAX_NODES = 12

VALID_LAYOUTS = ("line", "ring", "full", "mesh")


def free_port() -> int:
    """Ask the OS for an unused loopback port."""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def build_edges(nodes: list[str], layout: str) -> list[tuple[str, str]]:
    """Edges for a named layout. An empty layout is treated as a line."""
    if layout == "line":
        return [(nodes[i], nodes[i + 1]) for i in range(len(nodes) - 1)]
    if layout == "ring":
        return [(nodes[i], nodes[(i + 1) % len(nodes)]) for i in range(len(nodes))]
    if layout in ("full", "mesh"):
        return [(nodes[i], nodes[j])
                for i in range(len(nodes))
                for j in range(i + 1, len(nodes))]
    raise ValueError(f"unknown layout {layout!r}; expected one of {VALID_LAYOUTS}")


def validate_edges(nodes: list[str], edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """
    Check a caller-supplied edge list against a node list.

    Returns the edges with duplicates and ordering normalised. Raises
    ValueError naming the specific problem — a connection to a node that is not
    in the topology, a node connected to itself, or the same pair wired twice —
    because a silently dropped wire is a topology the operator did not draw, and
    the forwarding nodes would then move traffic over a path nobody asked for.
    """
    known = set(nodes)
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []

    for pair in edges:
        try:
            a, b = pair
        except (TypeError, ValueError) as exc:
            raise ValueError(f"link {pair!r} must be a (node_a, node_b) pair") from exc

        a, b = str(a), str(b)
        if a == b:
            raise ValueError(f"node {a!r} cannot be connected to itself")
        for endpoint in (a, b):
            if endpoint not in known:
                raise ValueError(
                    f"link {a}-{b} references unknown node {endpoint!r}; "
                    f"known nodes are {sorted(known)}"
                )
        key = (min(a, b), max(a, b))
        if key in seen:
            raise ValueError(
                f"duplicate link {key[0]}-{key[1]}; each pair may be wired once"
            )
        seen.add(key)
        ordered.append((a, b))

    return ordered


@dataclass
class FailureRecord:
    """One injected fault, kept so it can be undone and audited."""

    target: str
    kind: str                     # "node" | "link"
    injected_at: float
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "target": self.target,
            "kind": self.kind,
            "injected_at": self.injected_at,
            "detail": self.detail,
        }


class LabRuntime:
    """
    A live, breakable overlay.

    Owns the node objects and their lifecycle. Safe to use from a single event
    loop (the API server's); not designed for cross-thread use.
    """

    def __init__(self, on_event: Callable[[str, dict], None] | None = None):
        self.nodes: dict[str, ForwardingNode] = {}
        self.edges: list[tuple[str, str]] = []
        self.layout = "line"
        self.failures: list[FailureRecord] = []
        self.started_at: float | None = None
        self._heartbeats: list[asyncio.Task] = []
        self._on_event = on_event or (lambda _t, _p: None)
        self._history: list[dict] = []
        #: canonical link id -> (a, b). Built from `edges` so link ids never have
        #: to be split apart to find their endpoints — node ids are user-supplied
        #: and may legitimately contain the separator.
        self._links: dict[str, tuple[str, str]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return bool(self.nodes) and self.started_at is not None

    async def start(
        self,
        node_ids: list[str],
        layout: str = "line",
        heartbeat_interval_s: float = 1.0,
        shared_key: bytes | None = None,
        edges: list[tuple[str, str]] | None = None,
        f_thresh: int = 3,
        n: int = 5,
        r_thresh: int = 5,
    ) -> dict:
        """
        Build and start a topology.

        Rejects duplicates, an over-large topology, and an unknown layout rather
        than silently starting something other than what was asked for.

        `edges` supplies the connections explicitly. When it is given the
        layout is ignored — the caller drew the topology, so the topology is
        what gets started, not a shape derived from a preset. The edge list is
        validated against the node list first: a connection to a node that does
        not exist, a self-connection, or the same link twice is an error, not a
        silently dropped wire.
        """
        ids = [n.strip() for n in node_ids if n and n.strip()]
        if not ids:
            raise ValueError("at least one node id is required")
        if len(set(ids)) != len(ids):
            raise ValueError("node ids must be unique")
        if len(ids) > MAX_NODES:
            raise ValueError(f"at most {MAX_NODES} nodes are supported, got {len(ids)}")

        if edges is None:
            if layout not in VALID_LAYOUTS:
                raise ValueError(f"unknown layout {layout!r}; expected one of {VALID_LAYOUTS}")
            resolved_edges = build_edges(ids, layout)
        else:
            resolved_edges = validate_edges(ids, edges)
            layout = "custom"

        await self.stop()          # a restart replaces whatever was running

        key = shared_key if shared_key is not None else generate_key()
        ports = {nid: free_port() for nid in ids}

        for nid in ids:
            self.nodes[nid] = ForwardingNode(
                node_id=nid,
                host="127.0.0.1",
                port=ports[nid],
                shared_key=key,
                heartbeat_interval_s=heartbeat_interval_s,
                on_event=self._forward_event,
            )

        for a, b in resolved_edges:
            self.nodes[a].add_neighbour(b, "127.0.0.1", ports[b],
                                        f_thresh=f_thresh, n=n, r_thresh=r_thresh)
            self.nodes[b].add_neighbour(a, "127.0.0.1", ports[a],
                                        f_thresh=f_thresh, n=n, r_thresh=r_thresh)

        # Every node needs the full topology, not just its own links, or it can
        # never compute an alternate route.
        for node in self.nodes.values():
            node.install_topology(ids, resolved_edges)

        for node in self.nodes.values():
            await node.start()

        self.edges = resolved_edges
        self._links = {link_name(a, b): (a, b) for a, b in resolved_edges}
        self.layout = layout
        self.failures = []
        self.started_at = time.time()

        for node in self.nodes.values():
            self._heartbeats.append(
                asyncio.create_task(node.heartbeat_loop())
            )
            node.recompute_routes()

        self._emit("topology.started", {
            "nodes": ids, "layout": layout, "ports": ports,
        })
        return self.status()

    async def stop(self) -> None:
        """Tear everything down. Safe to call when nothing is running."""
        for task in self._heartbeats:
            task.cancel()
        self._heartbeats = []
        for node in list(self.nodes.values()):
            try:
                await node.stop()
            except Exception as exc:
                log.debug("stopping %s raised %s", node.node_id, exc)
        self.nodes = {}
        self.edges = []
        self._links = {}
        self.failures = []
        self.started_at = None

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """A snapshot the dashboard can render without guessing."""
        if not self.nodes:
            return {
                "running": False,
                "nodes": [],
                "links": [],
                "failures": [],
                "message": "No topology is running. Start one to use the Recovery Lab.",
            }

        failed_links = {
            f.target for f in self.failures if f.kind == "link"
        }
        failed_nodes = {
            f.target for f in self.failures if f.kind == "node"
        }

        nodes_out = []
        for nid, node in self.nodes.items():
            s = node.status()
            nodes_out.append({
                "node_id": nid,
                "port": node.port,
                "running": s["running"] and nid not in failed_nodes,
                "failed": nid in failed_nodes,
                "uptime_s": s["uptime_s"],
                "link_states": {n: v["state"] for n, v in s["neighbours"].items()},
                "routes": s["routes"],
                "counters": s["counters"],
            })

        link_states = {n["node_id"]: n["link_states"] for n in nodes_out}
        links_out = []
        for a, b in self.edges:
            lid = link_name(a, b)
            down = lid in failed_links or a in failed_nodes or b in failed_nodes
            links_out.append({
                "link_id": lid,
                "node_a_id": a,
                "node_b_id": b,
                "status": "DOWN" if down else "UP",
                "state_a": link_states.get(a, {}).get(b, "UNKNOWN"),
                "state_b": link_states.get(b, {}).get(a, "UNKNOWN"),
            })

        return {
            "running": self.running,
            "layout": self.layout,
            "started_at": self.started_at,
            "uptime_s": round(time.time() - self.started_at, 2) if self.started_at else 0.0,
            "nodes": nodes_out,
            "links": links_out,
            "failures": [f.as_dict() for f in self.failures],
        }

    def routes(self) -> dict[str, dict]:
        """Per-source route table, used to show alternate paths."""
        return {nid: node.recompute_routes() for nid, node in self.nodes.items()}

    def resolve_link(self, link_id: str) -> tuple[str, str]:
        """
        Endpoints of a link id.

        Looks the id up in the registry built at `start()`. Splitting the id on
        a separator was the old approach and it is wrong for any node id that
        contains one — `A-1-B` is three nodes' worth of ambiguity. The registry
        cannot be ambiguous, so the split is only a fallback for a caller
        passing an id this runtime never started.
        """
        if link_id in self._links:
            return self._links[link_id]
        a, _, b = link_id.partition("-")
        if a in self.nodes and b in self.nodes:
            return a, b
        raise KeyError(f"unknown link {link_id!r}")

    def link_id(self, a: str, b: str) -> str:
        """Canonical, order-independent link id for a pair of nodes."""
        return link_name(a, b)

    def detectors(self) -> dict[str, dict[str, dict]]:
        """
        Every node's view of every link it is directly attached to.

        Two nodes on the same link hold separate detectors — each measures the
        heartbeats it sends and receives itself — so this is reported per node
        rather than collapsed, because a one-sided disagreement is exactly the
        thing a single shared number would hide.
        """
        return {
            nid: node.detector_states()
            for nid, node in self.nodes.items()
        }

    def configure_detector(self, node_id: str, neighbour_id: str, **params) -> dict:
        """Re-tune one link's detector on one node. See ForwardingNode."""
        node = self.nodes.get(node_id)
        if node is None:
            raise KeyError(f"unknown node {node_id!r}")
        return node.configure_detector(neighbour_id, **params)

    def transitions(self, link_id: str | None = None) -> list[dict]:
        """
        Detector transitions the live nodes have actually made.

        Read from the nodes rather than from a log the lab writes itself, so it
        cannot disagree with the state the routers are acting on.
        """
        seen: list[dict] = []
        for node in self.nodes.values():
            seen.extend(node.transition_log())
        seen.sort(key=lambda t: t["ts"])
        if link_id is not None:
            seen = [t for t in seen if t["link_id"] == link_id]
        return seen

    def history(self, limit: int = 100) -> list[dict]:
        return self._history[-limit:]

    # ------------------------------------------------------------------
    # Fault injection
    # ------------------------------------------------------------------

    def _require_running(self) -> None:
        if not self.running:
            raise RuntimeError("no topology is running")

    def _incident_links(self, node_id: str) -> list[str]:
        """Every link touching a node. Used to isolate it across all routers."""
        return [
            link_name(a, b)
            for a, b in self.edges
            if a == node_id or b == node_id
        ]

    def _mark_links(self, link_ids: list[str], failed: bool) -> None:
        """
        Apply a link up/down verdict to every node's router.

        This must reach *all* nodes, not only the two endpoints: a node several
        hops away still needs to know the link is gone, otherwise it keeps a
        route that runs through the failure.

        Two phases on purpose. Every router is updated first, then every table is
        rebuilt. Clearing and rebuilding each node in turn would leave a window
        where a node's table is empty but its neighbours are already updated, and
        a delivery arriving in that window would be told "no route" for a network
        that is in fact reachable.
        """
        for node in self.nodes.values():
            for lid in link_ids:
                if failed:
                    node.router.mark_failed(lid)
                else:
                    node.router._failed_links.discard(lid)
        for node in self.nodes.values():
            node.table.clear()
            node.recompute_routes()

    async def fail_node(self, node_id: str) -> dict:
        """
        Take a node down for real.

        Its listener is closed, so it genuinely cannot forward. Peers then find
        no alternate route where none exists, and route around it where one does.
        """
        self._require_running()
        node = self.nodes.get(node_id)
        if node is None:
            raise KeyError(f"unknown node {node_id!r}")
        if any(f.target == node_id and f.kind == "node" for f in self.failures):
            return {"already_failed": True, "node_id": node_id, **self.status()}

        await node.stop()
        self.failures.append(FailureRecord(node_id, "node", time.time(),
                                           "listener closed"))

        self._mark_links(self._incident_links(node_id), failed=True)

        self._emit("recovery.failure_injected", {
            "kind": "node", "target": node_id,
        })
        return self.status()

    async def restore_node(self, node_id: str) -> dict:
        """Bring a failed node back and rejoin it to the topology."""
        self._require_running()
        node = self.nodes.get(node_id)
        if node is None:
            raise KeyError(f"unknown node {node_id!r}")
        self.failures = [
            f for f in self.failures
            if not (f.target == node_id and f.kind == "node")
        ]

        self._mark_links(self._incident_links(node_id), failed=False)
        await node.start()
        # node.stop() ended this node's heartbeat loop (it runs while _running), so
        # a restored node must get a new one. Without it the node is silent, its
        # neighbours never hear it recover, and routes through it stay down.
        self._heartbeats.append(asyncio.create_task(node.heartbeat_loop()))
        for other in self.nodes.values():
            if other is node:
                continue
            for nbr, detector in other.detectors.items():
                if nbr == node_id:
                    detector.reset()

        self._emit("recovery.node_restored", {"node_id": node_id})
        return self.status()

    async def fail_link(self, link_id: str) -> dict:
        """
        Mark one link down at every node without stopping either endpoint.

        This is a routing verdict, not a cosmetic one: `_mark_links` reaches every
        node's router and then rebuilds every forwarding table, so no node's
        next hop can still point across the failed link. Forwarding reads the
        rebuilt table, which means the packets genuinely cannot cross it — the
        delivery test after this is a real test of the link being down.
        """
        self._require_running()
        a, b = self.resolve_link(link_id)
        canonical = link_name(a, b)
        if any(f.target == link_id and f.kind == "link" for f in self.failures):
            return {"already_failed": True, "link_id": link_id, **self.status()}

        self.failures.append(FailureRecord(canonical, "link", time.time(), "marked down"))
        self._mark_links([canonical], failed=True)

        self._emit("recovery.failure_injected", {"kind": "link", "target": canonical})
        return self.status()

    async def restore_link(self, link_id: str) -> dict:
        self._require_running()
        a, b = self.resolve_link(link_id)
        canonical = link_name(a, b)
        self.failures = [
            f for f in self.failures
            if not (f.target == link_id and f.kind == "link")
            and not (f.target == canonical and f.kind == "link")
        ]
        self._mark_links([canonical], failed=False)
        self._emit("recovery.link_restored", {"link_id": canonical})
        return self.status()

    async def clear_failures(self) -> dict:
        """Undo every injected fault, restarting any node that was stopped."""
        self._require_running()
        for record in list(self.failures):
            if record.kind == "node":
                await self.restore_node(record.target)
            else:
                await self.restore_link(record.target)
        self.failures = []
        self._mark_links([lid for a, b in self.edges
                          for lid in (link_name(a, b),)], failed=False)
        return self.status()

    # ------------------------------------------------------------------
    # Verified traffic
    # ------------------------------------------------------------------

    async def send(self, source: str, destination: str, body: bytes = b"ping",
                   timeout: float = 5.0) -> dict:
        """
        Send one message and wait for the destination to confirm receipt.

        `delivered` is False whenever confirmation did not arrive — including
        when the source has no route at all. It is never inferred from a
        successful local write.
        """
        self._require_running()
        node = self.nodes.get(source)
        if node is None:
            raise KeyError(f"unknown source node {source!r}")
        if destination not in self.nodes:
            raise KeyError(f"unknown destination node {destination!r}")

        entry = self._resolve_route(node, destination)
        if entry is None:
            result = {
                "delivered": False,
                "source": source,
                "destination": destination,
                "reason": "no route to destination",
            }
            self._record(result)
            return result

        delivered = await node.send_data(
            node.host, node.port, destination=destination, body=body,
            timeout=timeout,
        )
        result = {
            "delivered": bool(delivered),
            "source": source,
            "destination": destination,
            "path": entry.path,
            "reason": None if delivered else "no acknowledgement from destination",
        }
        self._record(result)
        return result

    def _record(self, entry: dict) -> None:
        entry = {**entry, "ts": time.time()}
        self._history.append(entry)
        del self._history[:-500]
        self._emit(
            "transfer.delivery" if entry.get("delivered") else "transfer.delivery_failed",
            entry,
        )

    @staticmethod
    def _resolve_route(node, destination: str):
        """
        The current route to a destination, preferring the live router.

        The forwarding table is a cache, and a cache can lag a topology change by
        the width of a recompute. Consulting the router directly means a delivery
        is never refused merely because the cache had not been rebuilt yet — and,
        just as importantly, never accepted from a route the router no longer
        believes in.
        """
        cached = node.table.lookup(destination)
        result = node.router.best_route(
            node.node_id, destination, hop_limit=node.table.hop_limit
        )
        if not (result.reachable and len(result.path) > 1):
            return None
        if cached is not None and cached.path == result.path:
            return cached

        entry = ForwardEntry(
            destination=destination,
            next_hop=result.next_hop or "",
            path=result.path,
            version=result.version,
        )
        node.table._entries[destination] = entry
        return entry

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _forward_event(self, event_type: str, payload: dict) -> None:
        self._emit(event_type, payload)

    def _emit(self, event_type: str, payload: dict) -> None:
        try:
            self._on_event(event_type, payload)
        except Exception as exc:
            # A subscriber must never take down the lab.
            log.debug("lab event sink raised: %s", exc)
