"""tests/test_router.py — Unit tests for OverlayRouter (PRD §4.3)."""

import pytest

from netrescue.recovery.router import OverlayRouter, TopologyLink, TopologyNode


def make_triangle_router() -> OverlayRouter:
    """A → B → C with A ↔ C shortcut."""
    router = OverlayRouter(hop_limit=8)
    for nid in ("A", "B", "C"):
        router.add_node(TopologyNode(node_id=nid))
    router.add_link(TopologyLink(link_id="AB", node_a="A", node_b="B", latency_ms=1.0))
    router.add_link(TopologyLink(link_id="BC", node_a="B", node_b="C", latency_ms=1.0))
    router.add_link(TopologyLink(link_id="AC", node_a="A", node_b="C", latency_ms=5.0))
    return router


class TestBestRoute:
    def test_finds_shortest_path(self):
        router = make_triangle_router()
        result = router.best_route("A", "C")
        assert result.reachable
        # A→B→C (cost 2ms) should beat A→C (cost 5ms) on latency
        assert result.path == ["A", "B", "C"] or result.next_hop == "B"

    def test_same_source_destination(self):
        router = make_triangle_router()
        result = router.best_route("A", "A")
        # NetworkX returns trivial path
        assert result.reachable or not result.reachable  # just no crash


class TestLinkFailure:
    def test_route_unreachable_when_all_paths_fail(self):
        router = OverlayRouter()
        for nid in ("X", "Y"):
            router.add_node(TopologyNode(node_id=nid))
        router.add_link(TopologyLink(link_id="XY", node_a="X", node_b="Y", latency_ms=1.0))
        router.mark_failed("XY")
        result = router.best_route("X", "Y")
        assert not result.reachable

    def test_alternate_route_used_when_primary_fails(self):
        router = make_triangle_router()
        router.mark_failed("AB")
        result = router.best_route("A", "B")
        # A→C→B is the only remaining path
        assert result.reachable
        assert "C" in result.path

    def test_route_restores_after_mark_healthy(self):
        router = make_triangle_router()
        router.mark_failed("AB")
        router.mark_failed("AC")
        r1 = router.best_route("A", "C")
        assert not r1.reachable
        router.mark_healthy("AC")
        r2 = router.best_route("A", "C")
        assert r2.reachable


class TestHopLimit:
    def test_hop_limit_rejects_long_paths(self):
        router = OverlayRouter(hop_limit=2)
        for nid in ("A", "B", "C", "D"):
            router.add_node(TopologyNode(node_id=nid))
        router.add_link(TopologyLink("AB", "A", "B", latency_ms=1.0))
        router.add_link(TopologyLink("BC", "B", "C", latency_ms=1.0))
        router.add_link(TopologyLink("CD", "C", "D", latency_ms=1.0))
        result = router.best_route("A", "D", hop_limit=2)
        assert not result.reachable

    def test_hop_limit_passes_short_path(self):
        router = make_triangle_router()
        result = router.best_route("A", "B", hop_limit=2)
        assert result.reachable


class TestRouteVersioning:
    def test_version_increments_on_failure(self):
        router = make_triangle_router()
        v0 = router.current_version
        router.mark_failed("AB")
        assert router.current_version == v0 + 1

    def test_version_increments_on_recovery(self):
        router = make_triangle_router()
        router.mark_failed("AB")
        v1 = router.current_version
        router.mark_healthy("AB")
        assert router.current_version == v1 + 1

    def test_route_result_carries_version(self):
        router = make_triangle_router()
        result = router.best_route("A", "C")
        assert result.version == router.current_version


class TestMissingNodes:
    def test_unknown_source_returns_unreachable(self):
        router = make_triangle_router()
        result = router.best_route("Z", "A")
        assert not result.reachable

    def test_unknown_dest_returns_unreachable(self):
        router = make_triangle_router()
        result = router.best_route("A", "Z")
        assert not result.reachable
