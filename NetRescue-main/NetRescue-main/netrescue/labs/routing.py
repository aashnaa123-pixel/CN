"""
netrescue/labs/routing.py — Feature 4: shortest-path routing simulator.

A weighted, undirected graph of routers and links, solved with Dijkstra's
algorithm (E. W. Dijkstra, 1959). This is a textbook implementation written to
be read: every settle/relax step is recorded in a trace so the UI can show how
the answer was reached, not just the answer.

Cost semantics
--------------
`cost` is a unitless administrative metric, like an OSPF interface cost: the
route with the smallest SUM of link costs wins. It is not milliseconds, and the
simulator does not derive it from `delay_ms`, `loss` or `bandwidth_mbps`. Those
three attributes are carried only so other labs (TCP, integration) can describe
the path that was chosen. A cost must be finite and strictly positive;
Dijkstra's correctness proof requires non-negative edges, and zero-cost links
make the "alternatives" ill-defined, so both are rejected.

Links are bidirectional and symmetric (one cost for both directions). A link
that is `up=False` is ignored by the solver, exactly as if it were absent.

Determinism: ties are broken by node name, so identical inputs always give
identical routes, traces and alternatives.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field, replace
from typing import Iterable, Optional

MAX_NODES = 60
MAX_LINKS = 300


class GraphError(ValueError):
    """Invalid topology or parameters. The message is safe to show a user."""


def link_id(a: str, b: str) -> str:
    """Canonical id of the undirected link between two nodes: 'A-B' with A<B."""
    lo, hi = sorted((a, b))
    return f"{lo}-{hi}"


@dataclass(frozen=True)
class Link:
    a: str
    b: str
    cost: float
    delay_ms: float = 5.0          # one-way propagation, used by other labs
    loss: float = 0.0              # packet-loss probability, used by other labs
    bandwidth_mbps: float = 100.0  # used by other labs
    up: bool = True

    @property
    def id(self) -> str:
        return link_id(self.a, self.b)

    def other(self, node: str) -> str:
        return self.b if node == self.a else self.a

    def to_dict(self) -> dict:
        return {
            "id": self.id, "a": self.a, "b": self.b, "cost": self.cost,
            "delay_ms": self.delay_ms, "loss": self.loss,
            "bandwidth_mbps": self.bandwidth_mbps, "up": self.up,
        }


def _check_cost(cost: float) -> float:
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        raise GraphError(f"link cost must be a number, got {cost!r}")
    if not math.isfinite(cost):
        raise GraphError("link cost must be finite")
    if cost <= 0:
        raise GraphError(f"link cost must be greater than 0, got {cost}")
    return float(cost)


@dataclass
class Graph:
    nodes: list[str] = field(default_factory=list)
    links: dict[str, Link] = field(default_factory=dict)

    # -- editing ----------------------------------------------------------
    def add_node(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            raise GraphError("node name must not be empty")
        if len(name) > 24:
            raise GraphError("node name must be at most 24 characters")
        if name in self.nodes:
            raise GraphError(f"node {name!r} already exists")
        if len(self.nodes) >= MAX_NODES:
            raise GraphError(f"at most {MAX_NODES} nodes are supported")
        self.nodes.append(name)

    def remove_node(self, name: str) -> None:
        if name not in self.nodes:
            raise GraphError(f"unknown node {name!r}")
        self.nodes.remove(name)
        self.links = {k: l for k, l in self.links.items() if name not in (l.a, l.b)}

    def add_link(self, a: str, b: str, cost: float, **attrs) -> Link:
        for n in (a, b):
            if n not in self.nodes:
                raise GraphError(f"unknown node {n!r}")
        if a == b:
            raise GraphError("a link must connect two different nodes")
        lid = link_id(a, b)
        if lid in self.links:
            raise GraphError(f"link {lid} already exists")
        if len(self.links) >= MAX_LINKS:
            raise GraphError(f"at most {MAX_LINKS} links are supported")
        loss = attrs.get("loss", 0.0)
        if not 0.0 <= loss < 1.0:
            raise GraphError("loss must be in [0, 1)")
        if attrs.get("delay_ms", 5.0) < 0 or attrs.get("bandwidth_mbps", 100.0) <= 0:
            raise GraphError("delay_ms must be >= 0 and bandwidth_mbps > 0")
        link = Link(a=min(a, b), b=max(a, b), cost=_check_cost(cost), **attrs)
        self.links[lid] = link
        return link

    def remove_link(self, a: str, b: str) -> None:
        lid = link_id(a, b)
        if lid not in self.links:
            raise GraphError(f"unknown link {lid}")
        del self.links[lid]

    def set_cost(self, a: str, b: str, cost: float) -> None:
        lid = link_id(a, b)
        if lid not in self.links:
            raise GraphError(f"unknown link {lid}")
        self.links[lid] = replace(self.links[lid], cost=_check_cost(cost))

    def set_up(self, a: str, b: str, up: bool) -> None:
        lid = link_id(a, b)
        if lid not in self.links:
            raise GraphError(f"unknown link {lid}")
        self.links[lid] = replace(self.links[lid], up=up)

    def copy(self) -> "Graph":
        return Graph(nodes=list(self.nodes), links=dict(self.links))

    # -- queries ----------------------------------------------------------
    def neighbours(self, node: str) -> list[tuple[str, Link]]:
        out = [(l.other(node), l) for l in self.links.values()
               if l.up and node in (l.a, l.b)]
        out.sort(key=lambda t: t[0])
        return out

    def to_dict(self) -> dict:
        return {"nodes": list(self.nodes),
                "links": [l.to_dict() for l in sorted(self.links.values(), key=lambda l: l.id)]}

    @classmethod
    def from_dict(cls, data: dict) -> "Graph":
        g = cls()
        for n in data.get("nodes", []):
            g.add_node(n)
        for l in data.get("links", []):
            attrs = {k: l[k] for k in ("delay_ms", "loss", "bandwidth_mbps", "up") if k in l}
            g.add_link(l["a"], l["b"], l["cost"], **attrs)
        return g


# ---------------------------------------------------------------------------
# Dijkstra
# ---------------------------------------------------------------------------


@dataclass
class TraceStep:
    """One settled node and the relaxations it caused."""
    order: int
    node: str
    distance: float
    relaxed: list[dict]  # [{"to", "via_link", "old", "new", "improved"}]

    def to_dict(self) -> dict:
        return {"order": self.order, "node": self.node, "distance": self.distance,
                "relaxed": self.relaxed}


@dataclass
class Iteration:
    """
    The algorithm's state recorded at one point of the run.

    Iteration 0 is the initialisation (source = 0, everything else infinite).
    Iteration k >= 1 is recorded AFTER the k-th minimum-distance unvisited node
    has been selected, marked visited and its links relaxed. These are snapshots
    taken while the algorithm runs, not reconstructed afterwards.
    `None` in `distances` means infinity (no path found yet).
    """
    iteration: int
    selected: Optional[str]
    visited: list[str]
    distances: dict[str, Optional[float]]
    predecessors: dict[str, Optional[str]]
    relaxed: list[dict]
    note: str

    def to_dict(self) -> dict:
        return {"iteration": self.iteration, "selected": self.selected,
                "visited": self.visited, "distances": self.distances,
                "predecessors": self.predecessors, "relaxed": self.relaxed,
                "note": self.note}


@dataclass
class RouteResult:
    source: str
    target: str
    reachable: bool
    path: list[str]
    links: list[str]
    cost: Optional[float]
    visited: list[str]            # nodes in the order Dijkstra settled them
    distances: dict[str, Optional[float]]
    trace: list[TraceStep]
    predecessors: dict[str, Optional[str]] = field(default_factory=dict)
    pred_links: dict[str, str] = field(default_factory=dict)
    iterations: list[Iteration] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source, "target": self.target,
            "reachable": self.reachable, "path": self.path, "links": self.links,
            "cost": self.cost, "visited": self.visited,
            "distances": self.distances,
            "trace": [t.to_dict() for t in self.trace],
            "predecessors": self.predecessors,
            "iterations": [i.to_dict() for i in self.iterations],
        }

    def path_to(self, node: str) -> tuple[list[str], list[str]]:
        """(path, link ids) from the source to `node`, or ([], []) if unreachable."""
        if node != self.source and node not in self.pred_links:
            return [], []
        path, links, cur = [node], [], node
        while cur != self.source:
            links.append(self.pred_links[cur])
            cur = self.predecessors[cur]  # type: ignore[assignment]
            path.append(cur)
        path.reverse()
        links.reverse()
        return path, links


def dijkstra(graph: Graph, source: str, target: Optional[str] = None) -> RouteResult:
    """
    Single-source shortest paths over the links that are up.

    If `target` is given the search stops as soon as the target is settled
    (every later node has distance >= the target's), so `visited` shows the work
    actually needed. Without a target it settles every reachable node.
    """
    if source not in graph.nodes:
        raise GraphError(f"unknown source node {source!r}")
    if target is not None and target not in graph.nodes:
        raise GraphError(f"unknown destination node {target!r}")

    dist: dict[str, float] = {source: 0.0}
    prev: dict[str, tuple[str, str]] = {}          # node -> (previous node, link id)
    settled: set[str] = set()
    visited: list[str] = []
    trace: list[TraceStep] = []
    iterations: list[Iteration] = []
    heap: list[tuple[float, str]] = [(0.0, source)]

    def snapshot(selected: Optional[str], relaxed: list[dict], note: str) -> None:
        iterations.append(Iteration(
            iteration=len(iterations), selected=selected, visited=list(visited),
            distances={n: dist.get(n) for n in graph.nodes},
            predecessors={n: (prev[n][0] if n in prev else None) for n in graph.nodes},
            relaxed=relaxed, note=note))

    snapshot(None, [], f"Initialise: distance({source}) = 0, all other nodes = infinity.")

    while heap:
        d, u = heapq.heappop(heap)
        if u in settled:
            continue                                # stale heap entry
        settled.add(u)
        visited.append(u)
        relaxed: list[dict] = []
        if u != target:
            for v, link in graph.neighbours(u):
                if v in settled:
                    continue
                nd = d + link.cost
                old = dist.get(v)
                improved = old is None or nd < old
                if improved:
                    dist[v] = nd
                    prev[v] = (u, link.id)
                    heapq.heappush(heap, (nd, v))
                relaxed.append({"to": v, "via_link": link.id, "old": old,
                                "new": nd, "improved": improved})
        trace.append(TraceStep(len(visited), u, d, relaxed))
        snapshot(u, relaxed, f"Select {u} (smallest tentative distance {d:g} among unvisited "
                             f"nodes; ties go to the lowest node name) and relax its links.")
        if u == target:
            break

    unreached = [n for n in graph.nodes if n not in settled]
    if target is None and unreached:
        iterations[-1].note += (f" Finished: {', '.join(unreached)} can never be reached "
                                f"(distance stays infinity).")

    path: list[str] = []
    links: list[str] = []
    cost: Optional[float] = None
    reachable = target is not None and target in dist and target in settled
    result = RouteResult(
        source=source, target=target or "", reachable=bool(reachable), path=path,
        links=links, cost=cost, visited=visited,
        distances={n: dist.get(n) for n in graph.nodes}, trace=trace,
        predecessors={n: (prev[n][0] if n in prev else None) for n in graph.nodes},
        pred_links={n: prev[n][1] for n in prev}, iterations=iterations,
    )
    if reachable:
        result.path, result.links = result.path_to(target)
        result.cost = dist[target]
    return result


def routing_table(result: RouteResult) -> list[dict]:
    """
    One row per node, derived from a FULL Dijkstra run from `result.source`
    (call dijkstra(graph, source) without a target).

    next_hop is the first node after the source on the shortest path. The source
    row is explicit ("local"), and nodes the run never reached are 'unreachable'.
    Only meaningful when the run was not stopped early at a target.
    """
    rows = []
    for node in result.distances:
        if node == result.source:
            rows.append({"destination": node, "cost": 0.0, "next_hop": None,
                         "path": [node], "status": "source"})
            continue
        path, _ = result.path_to(node)
        reachable = bool(path) and node in result.visited
        rows.append({
            "destination": node,
            "cost": result.distances[node] if reachable else None,
            "next_hop": path[1] if reachable else None,
            "path": path if reachable else [],
            "status": "reachable" if reachable else "unreachable",
        })
    return rows


# ---------------------------------------------------------------------------
# Alternative routes (Yen's k-shortest loopless paths)
# ---------------------------------------------------------------------------


def _path_cost(graph: Graph, path: list[str]) -> float:
    return sum(graph.links[link_id(a, b)].cost for a, b in zip(path, path[1:]))


def k_shortest_paths(graph: Graph, source: str, target: str, k: int = 3) -> list[dict]:
    """
    Up to `k` loopless routes in non-decreasing cost order (Yen's algorithm).

    Yen's algorithm is used for the alternatives because plain Dijkstra only
    yields the single best route. The first entry always equals the Dijkstra
    route. Fewer than `k` entries means fewer loopless routes exist.
    """
    if k < 1:
        raise GraphError("k must be at least 1")
    first = dijkstra(graph, source, target)
    if not first.reachable:
        return []
    accepted: list[list[str]] = [first.path]
    candidates: list[tuple[float, list[str]]] = []

    for _ in range(1, k):
        last = accepted[-1]
        for i in range(len(last) - 1):
            spur = last[i]
            root = last[: i + 1]
            work = graph.copy()
            # Block the next hop of every accepted path sharing this root.
            for p in accepted:
                if p[: i + 1] == root and len(p) > i + 1:
                    lid = link_id(p[i], p[i + 1])
                    if lid in work.links:
                        work.set_up(p[i], p[i + 1], False)
            # Block the root's earlier nodes so the result stays loopless.
            for n in root[:-1]:
                for lk in list(work.links.values()):
                    if n in (lk.a, lk.b):
                        work.set_up(lk.a, lk.b, False)
            spur_route = dijkstra(work, spur, target)
            if spur_route.reachable:
                total = root[:-1] + spur_route.path
                if all(total != c[1] for c in candidates) and total not in accepted:
                    candidates.append((_path_cost(graph, total), total))
        if not candidates:
            break
        candidates.sort(key=lambda c: (c[0], c[1]))
        accepted.append(candidates.pop(0)[1])

    return [{"path": p, "links": [link_id(a, b) for a, b in zip(p, p[1:])],
             "cost": _path_cost(graph, p)} for p in accepted]


# ---------------------------------------------------------------------------
# What-if analysis
# ---------------------------------------------------------------------------

CHANGE_KINDS = ("link_down", "link_up", "cost_change")


def apply_change(graph: Graph, change: dict) -> Graph:
    """Return a copy of `graph` with one change applied. Never mutates the input."""
    kind = change.get("kind")
    if kind not in CHANGE_KINDS:
        raise GraphError(f"change kind must be one of {list(CHANGE_KINDS)}")
    a, b = change.get("a"), change.get("b")
    if not a or not b:
        raise GraphError("a change needs both link endpoints 'a' and 'b'")
    g = graph.copy()
    if kind == "link_down":
        g.set_up(a, b, False)
    elif kind == "link_up":
        g.set_up(a, b, True)
    else:
        if "cost" not in change:
            raise GraphError("a cost_change needs a 'cost'")
        g.set_cost(a, b, change["cost"])
    return g


def compare_routes(graph: Graph, source: str, target: str,
                   changes: Iterable[dict], k: int = 3) -> dict:
    """Solve before and after applying `changes`, and describe the difference."""
    before_graph = graph
    after_graph = graph
    for ch in changes:
        after_graph = apply_change(after_graph, ch)

    before = dijkstra(before_graph, source, target)
    after = dijkstra(after_graph, source, target)

    if before.reachable and after.reachable:
        rerouted = before.path != after.path
        delta = after.cost - before.cost
    else:
        rerouted = before.reachable != after.reachable or before.path != after.path
        delta = None

    if before.reachable and not after.reachable:
        summary = f"{target} became unreachable from {source}."
    elif not before.reachable and after.reachable:
        summary = f"{target} became reachable from {source}."
    elif not before.reachable:
        summary = f"{target} is unreachable from {source} before and after."
    elif rerouted:
        summary = (f"Route changed from {' → '.join(before.path)} (cost {before.cost:g}) to "
                   f"{' → '.join(after.path)} (cost {after.cost:g}).")
    else:
        summary = (f"Route unchanged: {' → '.join(after.path)}; "
                   f"cost {before.cost:g} → {after.cost:g}.")

    return {
        "before": {"graph": before_graph.to_dict(), "route": before.to_dict(),
                   "alternatives": k_shortest_paths(before_graph, source, target, k)},
        "after": {"graph": after_graph.to_dict(), "route": after.to_dict(),
                  "alternatives": k_shortest_paths(after_graph, source, target, k)},
        "rerouted": rerouted,
        "cost_delta": delta,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Deterministic example
# ---------------------------------------------------------------------------


def example_graph() -> Graph:
    """
    A six-router topology with distinct A->F routes (link costs):

        A-B 2, A-C 4, B-D 3, B-E 1, C-E 2, D-E 3, D-F 1, E-F 5

    Cheapest A->F is A-B-D-F (cost 6). If B-D fails, A-B-E-D-F (cost 2+1+3+1 = 7)
    takes over. Delay/loss/bandwidth are chosen so the transport labs show a
    visible difference between the two routes.
    """
    g = Graph()
    for n in "ABCDEF":
        g.add_node(n)
    g.add_link("A", "B", 2, delay_ms=5, bandwidth_mbps=100)
    g.add_link("A", "C", 4, delay_ms=8, bandwidth_mbps=50)
    g.add_link("B", "D", 3, delay_ms=6, bandwidth_mbps=100)
    g.add_link("B", "E", 1, delay_ms=4, bandwidth_mbps=100)
    g.add_link("C", "E", 2, delay_ms=10, bandwidth_mbps=50)
    g.add_link("D", "E", 3, delay_ms=12, bandwidth_mbps=20)
    g.add_link("D", "F", 1, delay_ms=5, bandwidth_mbps=100)
    g.add_link("E", "F", 5, delay_ms=15, bandwidth_mbps=20)
    return g
