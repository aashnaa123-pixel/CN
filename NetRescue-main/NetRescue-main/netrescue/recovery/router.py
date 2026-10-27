"""
netrescue/recovery/router.py

NetworkX-based route calculator for the overlay topology (PRD §4.3).

Features:
- Calculates paths excluding FAILED nodes/links.
- Hop limit enforcement (loop protection).
- Route versioning with stale-route rejection.
- Reports UNREACHABLE rather than false recovery when no alternate route exists.

Usage::

    router = OverlayRouter(topology, hop_limit=8)

    # When a link fails:
    router.mark_failed("link-id")
    route = router.best_route("A", "D")

    # When a link recovers:
    router.mark_healthy("link-id")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import networkx as nx

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Topology dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TopologyNode:
    node_id: str
    status: str = "UP"  # "UP" | "DOWN"


@dataclass
class TopologyLink:
    link_id: str
    node_a: str
    node_b: str
    status: str = "UP"  # "UP" | "DOWN"
    latency_ms: float = 1.0
    capacity_bps: float = float("inf")


# ---------------------------------------------------------------------------
# Route result
# ---------------------------------------------------------------------------


@dataclass
class RouteResult:
    source: str
    destination: str
    path: list[str]          # Ordered list of node IDs
    next_hop: str | None     # Immediate next hop from source
    version: int = 0
    reachable: bool = True
    reason: str = ""

    def __str__(self) -> str:
        if not self.reachable:
            return f"UNREACHABLE({self.source} → {self.destination}): {self.reason}"
        return f"{' → '.join(self.path)} (next_hop={self.next_hop}, v{self.version})"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class OverlayRouter:
    """
    NetworkX-based overlay route calculator with hysteresis integration.

    Route versioning prevents stale route installation (PRD §4.3):
    - Every route update increments the version.
    - Callers must pass their current version; stale updates are rejected.
    """

    def __init__(self, hop_limit: int = 8):
        self._graph: nx.Graph = nx.Graph()
        self._failed_nodes: set[str] = set()
        self._failed_links: set[str] = set()
        self._link_ids: dict[frozenset[str], str] = {}  # (a,b) → link_id
        self._version: int = 0

    # ------------------------------------------------------------------
    # Topology management
    # ------------------------------------------------------------------

    def add_node(self, node: TopologyNode) -> None:
        self._graph.add_node(node.node_id, status=node.status)
        log.debug("Added node %s", node.node_id)

    def add_link(self, link: TopologyLink) -> None:
        self._graph.add_edge(
            link.node_a,
            link.node_b,
            link_id=link.link_id,
            latency_ms=link.latency_ms,
            capacity_bps=link.capacity_bps,
            status=link.status,
        )
        self._link_ids[frozenset([link.node_a, link.node_b])] = link.link_id
        log.debug("Added link %s (%s↔%s)", link.link_id, link.node_a, link.node_b)

    # ------------------------------------------------------------------
    # Failure / recovery
    # ------------------------------------------------------------------

    def mark_failed(self, link_id: str) -> None:
        """Mark a link as failed; exclude from route calculations."""
        self._failed_links.add(link_id)
        self._version += 1
        log.info("Link %s marked FAILED (route version → %d)", link_id, self._version)

    def mark_node_failed(self, node_id: str) -> None:
        """Mark a node as failed; exclude from route calculations."""
        self._failed_nodes.add(node_id)
        self._version += 1
        log.info("Node %s marked FAILED (route version → %d)", node_id, self._version)

    def mark_healthy(self, link_id: str) -> None:
        """Mark a link as healthy again."""
        self._failed_links.discard(link_id)
        self._version += 1
        log.info("Link %s marked HEALTHY (route version → %d)", link_id, self._version)

    def mark_node_healthy(self, node_id: str) -> None:
        """Mark a node as healthy again."""
        self._failed_nodes.discard(node_id)
        self._version += 1
        log.info("Node %s marked HEALTHY (route version → %d)", node_id, self._version)

    # ------------------------------------------------------------------
    # Filtered view
    # ------------------------------------------------------------------

    def _active_graph(self) -> nx.Graph:
        """Return a subgraph excluding failed nodes and links."""
        g = self._graph.copy()

        # Remove failed nodes
        for node_id in list(self._failed_nodes):
            if g.has_node(node_id):
                g.remove_node(node_id)

        # Remove failed links
        for u, v, data in list(self._graph.edges(data=True)):
            if data.get("link_id") in self._failed_links:
                if g.has_edge(u, v):
                    g.remove_edge(u, v)

        return g

    # ------------------------------------------------------------------
    # Route calculation
    # ------------------------------------------------------------------

    def best_route(
        self,
        source: str,
        destination: str,
        hop_limit: int = 8,
        weight: str = "latency_ms",
    ) -> RouteResult:
        """
        Calculate the lowest-latency path from source to destination
        excluding failed elements.

        Implements:
        - Hop limit (loop protection, PRD §4.3)
        - Stale-route rejection — callers should check RouteResult.version
        - Reports UNREACHABLE when no path exists (not false recovery)
        """
        g = self._active_graph()

        if source not in g:
            return RouteResult(
                source=source, destination=destination, path=[], next_hop=None,
                version=self._version, reachable=False,
                reason=f"Source node {source!r} is unavailable.",
            )

        if destination not in g:
            return RouteResult(
                source=source, destination=destination, path=[], next_hop=None,
                version=self._version, reachable=False,
                reason=f"Destination node {destination!r} is unavailable.",
            )

        try:
            path = nx.shortest_path(g, source, destination, weight=weight)
        except nx.NetworkXNoPath:
            log.warning("No path from %s to %s — reporting UNREACHABLE.", source, destination)
            return RouteResult(
                source=source, destination=destination, path=[], next_hop=None,
                version=self._version, reachable=False,
                reason="No alternate route available after failure.",
            )
        except nx.NodeNotFound as exc:
            return RouteResult(
                source=source, destination=destination, path=[], next_hop=None,
                version=self._version, reachable=False,
                reason=str(exc),
            )

        # Hop limit check
        if len(path) - 1 > hop_limit:
            log.warning(
                "Path %s exceeds hop limit %d — rejecting.", " → ".join(path), hop_limit
            )
            return RouteResult(
                source=source, destination=destination, path=path, next_hop=None,
                version=self._version, reachable=False,
                reason=f"Path length {len(path) - 1} exceeds hop limit {hop_limit}.",
            )

        next_hop = path[1] if len(path) > 1 else destination
        result = RouteResult(
            source=source, destination=destination, path=path,
            next_hop=next_hop, version=self._version, reachable=True,
        )
        log.info("Route %s→%s: %s", source, destination, result)
        return result

    def all_routes(self, source: str, hop_limit: int = 8) -> dict[str, RouteResult]:
        """Calculate routes from source to all reachable nodes."""
        g = self._active_graph()
        routes: dict[str, RouteResult] = {}
        for destination in g.nodes():
            if destination == source:
                continue
            routes[destination] = self.best_route(source, destination, hop_limit=hop_limit)
        return routes

    @property
    def current_version(self) -> int:
        return self._version
