"""
tests/test_routing_tables.py — Dijkstra iteration states and the final routing table.

The iteration snapshots are recorded while the algorithm runs, so these tests
check the real intermediate states against hand-worked values, and the final
table against networkx on random graphs.
"""

import random

import networkx as nx
import pytest
from fastapi.testclient import TestClient

from netrescue.labs.routing import Graph, GraphError, dijkstra, example_graph, routing_table

INF = None   # infinity is None in the API / snapshots


def table(graph, source):
    return {r["destination"]: r for r in routing_table(dijkstra(graph, source))}


def test_iterations_match_hand_worked_example():
    # A-B 2, A-C 4, B-D 3, B-E 1, C-E 2, D-E 3, D-F 1, E-F 5 ; source A
    its = dijkstra(example_graph(), "A").iterations
    assert [i.selected for i in its] == [None, "A", "B", "E", "C", "D", "F"]
    assert [i.iteration for i in its] == list(range(7))

    assert its[0].visited == [] and its[0].distances == {
        "A": 0, "B": INF, "C": INF, "D": INF, "E": INF, "F": INF}
    assert its[1].distances == {"A": 0, "B": 2, "C": 4, "D": INF, "E": INF, "F": INF}
    assert its[2].selected == "B" and its[2].visited == ["A", "B"]
    assert its[2].distances == {"A": 0, "B": 2, "C": 4, "D": 5, "E": 3, "F": INF}
    assert its[2].predecessors == {"A": None, "B": "A", "C": "A", "D": "B", "E": "B", "F": None}
    # E settles: C stays 4 (via E would be 5), D stays 5 (via E would be 6), F becomes 8
    assert its[3].distances["F"] == 8 and its[3].predecessors["F"] == "E"
    assert its[3].distances["C"] == 4 and its[3].predecessors["C"] == "A"
    # D settles: F improves 8 -> 6 and its predecessor changes E -> D
    assert its[5].distances["F"] == 6 and its[5].predecessors["F"] == "D"
    assert its[5].relaxed == [
        {"to": "F", "via_link": "D-F", "old": 8, "new": 6, "improved": True}]
    assert its[6].visited == ["A", "B", "E", "C", "D", "F"]


def test_iterations_are_internally_consistent():
    r = dijkstra(example_graph(), "A")
    prev_d = None
    for k, it in enumerate(r.iterations):
        assert len(it.visited) == k                              # one node finalised per iteration
        if k:
            assert it.visited[-1] == it.selected
        for n in it.visited:                                      # finalised distances never change
            assert it.distances[n] == r.distances[n]
        if prev_d:
            for n, d in it.distances.items():                     # tentative distances only decrease
                if prev_d[n] is not None:
                    assert d is not None and d <= prev_d[n]
        prev_d = it.distances
    # snapshots are copies, not views of one mutating dict
    assert r.iterations[0].distances["B"] is None


def test_infinity_is_distinct_from_zero():
    g = Graph()
    for n in "ABC":
        g.add_node(n)
    g.add_link("A", "B", 1)
    r = dijkstra(g, "A")
    final = r.iterations[-1]
    assert final.distances == {"A": 0, "B": 1, "C": None}
    assert final.distances["A"] == 0 and final.distances["C"] is None
    assert "C" in final.note and "infinity" in final.note


def test_tie_break_is_lowest_node_name():
    g = Graph()
    for n in "STXY":
        g.add_node(n)
    g.add_link("S", "X", 1); g.add_link("S", "Y", 1)
    g.add_link("X", "T", 1); g.add_link("Y", "T", 1)
    its = dijkstra(g, "S").iterations
    assert [i.selected for i in its[1:]] == ["S", "X", "Y", "T"]  # X before Y at equal distance 1
    assert table(g, "S")["T"]["path"] == ["S", "X", "T"]           # first-found equal-cost path kept
    assert table(g, "S")["T"]["next_hop"] == "X"


def test_final_routing_table_rows():
    t = table(example_graph(), "A")
    assert t["A"] == {"destination": "A", "cost": 0, "next_hop": None, "path": ["A"], "status": "source"}
    assert t["F"] == {"destination": "F", "cost": 6, "next_hop": "B",
                      "path": ["A", "B", "D", "F"], "status": "reachable"}
    assert t["C"]["next_hop"] == "C" and t["C"]["cost"] == 4
    for row in t.values():
        if row["status"] == "reachable":
            assert row["next_hop"] == row["path"][1] and row["path"][0] == "A"


def test_table_changes_with_weight_and_with_link_state():
    g = example_graph()
    assert table(g, "A")["F"]["cost"] == 6
    g.set_cost("B", "D", 10)                       # weight change
    t = table(g, "A")
    assert t["F"]["cost"] == 7 and t["F"]["path"] == ["A", "B", "E", "D", "F"]
    assert t["D"]["cost"] == 6 and t["D"]["next_hop"] == "B"
    g.set_up("D", "F", False)                      # link disabled
    g.set_up("E", "F", False)
    t = table(g, "A")
    assert t["F"]["status"] == "unreachable" and t["F"]["cost"] is None
    assert t["F"]["next_hop"] is None and t["F"]["path"] == []
    assert t["E"]["status"] == "reachable"


def test_disconnected_graph_and_isolated_node():
    g = Graph()
    for n in "ABCD":
        g.add_node(n)
    g.add_link("A", "B", 2)
    g.add_link("C", "D", 1)
    t = table(g, "A")
    assert [t[n]["status"] for n in "ABCD"] == ["source", "reachable", "unreachable", "unreachable"]
    assert table(g, "C")["D"]["next_hop"] == "D"


def test_source_equals_destination_and_single_node():
    g = Graph()
    g.add_node("S")
    r = dijkstra(g, "S", "S")
    assert r.reachable and r.path == ["S"] and r.cost == 0 and r.links == []
    assert table(g, "S") == {"S": {"destination": "S", "cost": 0, "next_hop": None,
                                   "path": ["S"], "status": "source"}}


def test_invalid_inputs():
    with pytest.raises(GraphError):
        dijkstra(example_graph(), "Z")
    g = example_graph()
    for bad in (0, -2, float("nan"), float("inf")):
        with pytest.raises(GraphError):
            g.set_cost("A", "B", bad)


def test_table_matches_networkx_on_random_graphs():
    rng = random.Random(1234)
    for trial in range(40):
        n = rng.randint(2, 12)
        g, ng = Graph(), nx.Graph()
        names = [f"N{i}" for i in range(n)]
        for name in names:
            g.add_node(name)
            ng.add_node(name)
        for i in range(n):
            for j in range(i + 1, n):
                if rng.random() < 0.3:
                    c = rng.randint(1, 9)
                    g.add_link(names[i], names[j], c)
                    ng.add_edge(names[i], names[j], weight=c)
        for lk in list(g.links.values()):                 # randomly disable some links
            if rng.random() < 0.15:
                g.set_up(lk.a, lk.b, False)
                ng.remove_edge(lk.a, lk.b)
        src = rng.choice(names)
        expected = nx.single_source_dijkstra_path_length(ng, src, weight="weight")
        for row in routing_table(dijkstra(g, src)):
            d = row["destination"]
            if d in expected:
                assert row["cost"] == expected[d], (trial, d)
                assert row["status"] in ("source", "reachable")
                # the reported path is a real path in the graph with the reported cost
                path = row["path"]
                assert sum(ng[a][b]["weight"] for a, b in zip(path, path[1:])) == expected[d]
                if d != src:
                    assert row["next_hop"] == path[1]
            else:
                assert row["status"] == "unreachable" and row["cost"] is None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

OP = {"X-NetRescue-Principal": "t", "X-NetRescue-Role": "operator"}


@pytest.fixture(scope="module")
def client():
    from apps.api.main import app
    with TestClient(app) as c:
        yield c


def _graph(c):
    return c.get("/api/labs/routing/example", headers=OP).json()["graph"]


def test_api_returns_iterations_and_routing_table(client):
    g = _graph(client)
    body = client.post("/api/labs/routing/solve", headers=OP,
                       json={"graph": g, "source": "A", "target": "F"}).json()
    assert len(body["iterations"]) == 7
    assert body["iterations"][0]["distances"]["B"] is None
    assert body["route"]["path"] == ["A", "B", "D", "F"] and body["route"]["cost"] == 6
    rows = {r["destination"]: r for r in body["routing_table"]}
    assert rows["F"]["next_hop"] == "B" and rows["A"]["status"] == "source"
    assert body["execution"]["mode"] == "SIMULATION"
    assert "not a routing protocol" in body["terminology"]


def test_api_table_reacts_to_weight_and_disabled_link(client):
    g = _graph(client)
    for l in g["links"]:
        if (l["a"], l["b"]) == ("B", "D"):
            l["cost"] = 10
    body = client.post("/api/labs/routing/solve", headers=OP,
                       json={"graph": g, "source": "A", "target": "F"}).json()
    assert body["route"]["cost"] == 7
    for l in g["links"]:
        if l["b"] == "F":
            l["up"] = False
    body = client.post("/api/labs/routing/solve", headers=OP,
                       json={"graph": g, "source": "A", "target": "F"}).json()
    assert body["route"]["reachable"] is False and body["route"]["cost"] is None
    f = next(r for r in body["routing_table"] if r["destination"] == "F")
    assert f["status"] == "unreachable"


def test_api_other_source_changes_next_hops(client):
    g = _graph(client)
    body = client.post("/api/labs/routing/solve", headers=OP,
                       json={"graph": g, "source": "F", "target": "A"}).json()
    rows = {r["destination"]: r for r in body["routing_table"]}
    assert rows["F"]["status"] == "source" and rows["A"]["next_hop"] == "D"
    assert rows["A"]["path"] == ["F", "D", "B", "A"]


def test_api_validation(client):
    g = _graph(client)
    assert client.post("/api/labs/routing/solve", headers=OP,
                       json={"graph": g, "source": "ZZ", "target": "F"}).status_code == 422
    g["links"][0]["cost"] = 0
    assert client.post("/api/labs/routing/solve", headers=OP,
                       json={"graph": g, "source": "A", "target": "F"}).status_code == 422
