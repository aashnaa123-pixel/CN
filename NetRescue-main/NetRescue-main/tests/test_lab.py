"""
tests/test_lab.py — the live, breakable overlay behind the Recovery Lab.

Everything here runs real nodes on real sockets and asserts on observed
behaviour: delivery is confirmed by the destination, failures are real, and an
unreachable destination is reported as unreachable rather than as success.
"""

import asyncio

import pytest

from netrescue.lab import (
    MAX_NODES,
    VALID_LAYOUTS,
    LabRuntime,
    build_edges,
    free_port,
)


@pytest.fixture
async def lab():
    runtime = LabRuntime()
    try:
        yield runtime
    finally:
        await runtime.stop()


# ---------------------------------------------------------------------------
# Topology construction
# ---------------------------------------------------------------------------


class TestTopologyValidation:
    def test_line_edges(self):
        assert build_edges(["A", "B", "C"], "line") == [("A", "B"), ("B", "C")]

    def test_ring_edges(self):
        assert build_edges(["A", "B", "C"], "ring") == [
            ("A", "B"), ("B", "C"), ("C", "A")
        ]

    def test_full_layout_is_complete(self):
        edges = build_edges(["A", "B", "C", "D"], "full")
        assert len(edges) == 6
        assert ("A", "D") in edges

    def test_unknown_layout_rejected(self):
        with pytest.raises(ValueError, match="unknown layout"):
            build_edges(["A", "B"], "spiral")

    def test_layouts_are_declared(self):
        assert set(VALID_LAYOUTS) >= {"line", "ring", "full"}


@pytest.mark.asyncio
class TestStartup:
    async def test_starts_and_reports_status(self, lab):
        status = await lab.start(["A", "B", "C"], "line")
        assert status["running"] is True
        assert [n["node_id"] for n in status["nodes"]] == ["A", "B", "C"]
        assert len(status["links"]) == 2
        assert all(link["status"] == "UP" for link in status["links"])

    async def test_empty_ids_rejected(self, lab):
        with pytest.raises(ValueError, match="at least one node"):
            await lab.start([], "line")

    async def test_duplicate_ids_rejected(self, lab):
        with pytest.raises(ValueError, match="unique"):
            await lab.start(["A", "A"], "line")

    async def test_oversized_topology_rejected(self, lab):
        ids = [f"N{i}" for i in range(MAX_NODES + 1)]
        with pytest.raises(ValueError, match="at most"):
            await lab.start(ids, "full")

    async def test_unknown_layout_rejected(self, lab):
        with pytest.raises(ValueError, match="unknown layout"):
            await lab.start(["A", "B"], "hyperloop")

    async def test_restart_replaces_previous_topology(self, lab):
        await lab.start(["A", "B", "C"], "line")
        await lab.start(["X", "Y"], "line")
        ids = [n["node_id"] for n in lab.status()["nodes"]]
        assert ids == ["X", "Y"]

    async def test_delivery_survives_a_stale_forwarding_table(self, lab):
        """
        Regression: a delivery must not be refused just because the forwarding
        table has not been rebuilt yet.

        The route cache is deliberately emptied here, which is the window a
        topology change opens. The lab consults the router, so the message still
        goes through — and conversely, a route the router no longer believes in is
        never used.
        """
        await lab.start(["A", "B", "C"], "line")
        assert (await lab.send("A", "C", b"x", timeout=3.0))["delivered"] is True

        # Simulate the stale cache a mid-recompute delivery would see.
        lab.nodes["A"].table.clear()
        assert lab.nodes["A"].table.lookup("C") is None

        result = await lab.send("A", "C", b"y", timeout=3.0)
        assert result["delivered"] is True, (
            "a cached-but-empty table must not cause a false 'no route'"
        )

    async def test_stale_table_cannot_smuggle_a_dead_route(self, lab):
        """The other half: a cached entry for a failed link must not be used."""
        await lab.start(["A", "B", "C"], "line")
        await lab.fail_node("B")

        # Put a now-invalid entry back in the table, as a late advertisement would.
        from netrescue.forwarding.node import ForwardEntry
        lab.nodes["A"].table._entries["C"] = ForwardEntry(
            destination="C", next_hop="B", path=["A", "B", "C"], version=99
        )
        result = await lab.send("A", "C", b"z", timeout=2.0)
        assert result["delivered"] is False, (
            "a cached route through a dead node must not be used"
        )
        assert "no route" in result["reason"]

    async def test_operations_refuse_when_not_running(self, lab):
        with pytest.raises(RuntimeError, match="no topology"):
            await lab.send("A", "B")

    async def test_status_when_idle_is_honest(self, lab):
        status = lab.status()
        assert status["running"] is False
        assert status["nodes"] == []
        assert "Start one" in status["message"]


# ---------------------------------------------------------------------------
# Verified delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDelivery:
    async def test_direct_delivery_is_confirmed(self, lab):
        await lab.start(["A", "B"], "line")
        result = await lab.send("A", "B", b"hello")
        assert result["delivered"] is True, "delivery must be confirmed by the destination"
        assert result["path"] == ["A", "B"]

    async def test_multi_hop_delivery_is_confirmed(self, lab):
        await lab.start(["A", "B", "C", "D"], "line")
        result = await lab.send("A", "D", b"hello")
        assert result["delivered"] is True
        assert result["path"] == ["A", "B", "C", "D"]

    async def test_unknown_nodes_rejected(self, lab):
        await lab.start(["A", "B"], "line")
        with pytest.raises(KeyError):
            await lab.send("Z", "B")
        with pytest.raises(KeyError):
            await lab.send("A", "Z")

    async def test_delivery_is_recorded_in_history(self, lab):
        await lab.start(["A", "B"], "line")
        await lab.send("A", "B", b"one")
        history = lab.history()
        assert history and history[-1]["delivered"] is True


# ---------------------------------------------------------------------------
# Failure and recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFailureAndRecovery:
    async def test_node_failure_closes_the_real_listener(self, lab):
        """
        The fault must be real, not a flag.

        After fail_node the port must genuinely refuse connections — that is what
        makes any subsequent routing decision meaningful.
        """
        await lab.start(["A", "B"], "line")
        port = lab.nodes["B"].port

        # Confirm it is reachable before the fault.
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=2.0
        )
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

        await lab.fail_node("B")

        with pytest.raises(OSError):
            await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=2.0
            )

    async def test_line_topology_loses_the_path(self, lab):
        """A-B-C with B dead: A cannot reach C. That must be reported, not faked."""
        await lab.start(["A", "B", "C"], "line")
        await lab.fail_node("B")
        routes = lab.routes()["A"]
        assert routes["C"]["status"] == "UNREACHABLE"

        result = await lab.send("A", "C", b"x", timeout=2.0)
        assert result["delivered"] is False
        assert "no route" in result["reason"]

    async def test_ring_routes_around_a_dead_node(self, lab):
        """
        The headline claim: kill B in ring A-B-C-D and A still reaches C — the
        node whose only short path ran through B — by going A -> D -> C.

        The destination has to acknowledge for this to count as recovery; a
        changed route table alone is not evidence.
        """
        await lab.start(["A", "B", "C", "D"], "ring")

        # Before the fault, A's short path to C runs through B.
        before = lab.routes()["A"]
        assert before["C"]["path"] == ["A", "B", "C"]

        await lab.fail_node("B")
        result = await lab.send("A", "C", b"after", timeout=4.0)
        assert result["delivered"] is True, (
            "A must still reach C via D; the destination has to confirm it"
        )
        assert result["path"] == ["A", "D", "C"]

    async def test_a_dead_node_cannot_be_reached_at_all(self, lab):
        """
        The honest counterpart: a killed node is gone.

        There is no path to B from anywhere once B is down, and the lab must say
        UNREACHABLE rather than pretend a route exists.
        """
        await lab.start(["A", "B", "C", "D"], "ring")
        await lab.fail_node("B")

        for source in ("A", "C", "D"):
            routes = lab.routes()[source]
            assert routes["B"]["status"] == "UNREACHABLE", (
                f"{source} must not appear to have a path to the dead node B"
            )

        result = await lab.send("A", "B", b"anyone there?", timeout=2.0)
        assert result["delivered"] is False
        assert "no route" in result["reason"]

    async def test_restoring_a_node_rejoins_the_topology(self, lab):
        await lab.start(["A", "B", "C"], "line")
        await lab.fail_node("B")
        assert lab.routes()["A"]["C"]["status"] == "UNREACHABLE"

        await lab.restore_node("B")
        assert lab.routes()["A"]["C"]["status"] == "OK"
        result = await lab.send("A", "C", b"back", timeout=4.0)
        assert result["delivered"] is True

    async def test_restored_node_resumes_sending_heartbeats(self, lab):
        """node.stop() ends its heartbeat loop; restore must start a new one, or the
        node stays silent and its neighbours never see it recover."""
        await lab.start(["A", "B", "C"], "line")
        await lab.fail_node("B")
        await lab.restore_node("B")
        b = lab.nodes["B"]
        before = b.sent
        await asyncio.sleep(b.heartbeat_interval_s * 3 + 0.5)
        assert b.sent > before, "a restored node must keep sending heartbeats"

    async def test_link_failure_isolates_only_that_link(self, lab):
        await lab.start(["A", "B", "C"], "line")
        await lab.fail_link("A-B")
        assert lab.routes()["A"]["B"]["status"] == "UNREACHABLE"
        assert lab.routes()["A"]["C"]["status"] == "UNREACHABLE"
        # B and C still talk to each other.
        assert (await lab.send("B", "C", b"ok", timeout=3.0))["delivered"] is True

    async def test_link_restore(self, lab):
        await lab.start(["A", "B"], "line")
        await lab.fail_link("A-B")
        await lab.restore_link("A-B")
        assert lab.routes()["A"]["B"]["status"] == "OK"

    async def test_failing_twice_is_idempotent(self, lab):
        await lab.start(["A", "B"], "line")
        first = await lab.fail_node("B")
        second = await lab.fail_node("B")
        assert "already_failed" in second
        assert len(first["failures"]) == 1

    async def test_failures_are_audited_with_timestamps(self, lab):
        await lab.start(["A", "B", "C"], "line")
        await lab.fail_node("B")
        record = lab.status()["failures"][0]
        assert record["target"] == "B"
        assert record["kind"] == "node"
        assert record["injected_at"] > 0

    async def test_clear_failures_recovers_everything(self, lab):
        await lab.start(["A", "B", "C", "D"], "ring")
        await lab.fail_node("B")
        await lab.fail_link("C-D")
        await lab.clear_failures()
        assert lab.status()["failures"] == []
        assert (await lab.send("A", "B", b"x", timeout=4.0))["delivered"] is True

    async def test_unknown_failure_targets_rejected(self, lab):
        await lab.start(["A", "B"], "line")
        with pytest.raises(KeyError):
            await lab.fail_node("NOPE")
        with pytest.raises(KeyError):
            await lab.fail_link("A-NOPE")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEvents:
    async def test_events_reach_the_subscriber(self):
        seen = []
        runtime = LabRuntime(on_event=lambda t, p: seen.append((t, p)))
        try:
            await runtime.start(["A", "B", "C", "D"], "ring")
            await runtime.fail_node("B")
            await runtime.send("A", "C", b"hi", timeout=4.0)
            types = [t for t, _ in seen]
            assert "topology.started" in types
            assert "recovery.failure_injected" in types
            assert "transfer.delivery" in types
        finally:
            await runtime.stop()

    async def test_a_broken_subscriber_cannot_take_down_the_lab(self):
        def explode(_t, _p):
            raise RuntimeError("subscriber is broken")

        runtime = LabRuntime(on_event=explode)
        try:
            await runtime.start(["A", "B"], "line")
            assert (await runtime.send("A", "B", b"x"))["delivered"] is True
        finally:
            await runtime.stop()

    async def test_failed_delivery_emits_a_failure_event(self):
        seen = []
        runtime = LabRuntime(on_event=lambda t, p: seen.append(t))
        try:
            await runtime.start(["A", "B", "C"], "line")
            await runtime.fail_node("B")
            await runtime.send("A", "C", b"x", timeout=2.0)
            assert "transfer.delivery_failed" in seen
        finally:
            await runtime.stop()
