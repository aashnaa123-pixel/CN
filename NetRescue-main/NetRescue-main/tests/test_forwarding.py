"""
tests/test_forwarding.py — real overlay forwarding over TCP sockets (Feature A).

These tests start actual node processes-in-a-task on real loopback sockets. No
mocks: if forwarding, alternate-route recovery, loop suppression or stale-route
rejection breaks, these fail.
"""

import asyncio

import pytest

from netrescue.forwarding import (
    ForwardEntry,
    ForwardingNode,
    ForwardingTable,
    Message,
    ProtocolError,
    decode,
    link_name,
    sign_payload,
    verify_payload,
)
from netrescue.security.hmac_auth import generate_key


def free_ports(n: int) -> list[int]:
    import socket
    ports = []
    for _ in range(n):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        ports.append(s.getsockname()[1])
        s.close()
    return ports


# ---------------------------------------------------------------------------
# Wire protocol
# ---------------------------------------------------------------------------


class TestFraming:
    def test_round_trip(self):
        msg = Message(4, {"src": "A", "dst": "C", "body": "hello"})
        assert decode(msg.encode()) == msg

    def test_rejects_bad_magic(self):
        raw = bytearray(Message(4, {"a": 1}).encode())
        raw[0:2] = b"XX"
        with pytest.raises(ProtocolError, match="magic"):
            decode(bytes(raw))

    def test_rejects_bad_version(self):
        raw = bytearray(Message(4, {"a": 1}).encode())
        raw[2] = 99
        with pytest.raises(ProtocolError, match="version"):
            decode(bytes(raw))

    def test_rejects_truncated_frame(self):
        with pytest.raises(ProtocolError):
            decode(b"NR")

    def test_rejects_oversized_declared_length(self):
        import struct
        from netrescue.forwarding.node import HEADER, MAGIC, MAX_PAYLOAD

        raw = HEADER.pack(MAGIC, 1, 4, 0, MAX_PAYLOAD + 1)
        with pytest.raises(ProtocolError, match="exceeds"):
            decode(raw)

    def test_rejects_non_object_payload(self):
        import struct
        from netrescue.forwarding.node import HEADER, MAGIC

        body = b"[1,2,3]"
        raw = HEADER.pack(MAGIC, 1, 4, 0, len(body)) + body
        with pytest.raises(ProtocolError, match="JSON object"):
            decode(raw)


class TestSigning:
    def test_valid_mac_passes(self):
        key = generate_key()
        msg = sign_payload(Message(4, {"a": 1}), key)
        assert verify_payload(msg, key)

    def test_tampered_payload_fails(self):
        key = generate_key()
        msg = sign_payload(Message(4, {"a": 1}), key)
        msg.payload["a"] = 999
        assert not verify_payload(msg, key)

    def test_wrong_key_fails(self):
        msg = sign_payload(Message(4, {"a": 1}), generate_key())
        assert not verify_payload(msg, generate_key())


# ---------------------------------------------------------------------------
# Forwarding table
# ---------------------------------------------------------------------------


class TestForwardingTable:
    def test_install_and_lookup(self):
        t = ForwardingTable("A")
        assert t.install(ForwardEntry("C", "B", ["A", "B", "C"], version=1))
        assert t.lookup("C").next_hop == "B"

    def test_rejects_stale_route(self):
        t = ForwardingTable("A")
        t.install(ForwardEntry("C", "B", ["A", "B", "C"], version=5))
        assert not t.install(ForwardEntry("C", "D", ["A", "D", "C"], version=2))
        assert t.lookup("C").next_hop == "B"

    def test_accepts_newer_route(self):
        t = ForwardingTable("A")
        t.install(ForwardEntry("C", "B", ["A", "B", "C"], version=1))
        assert t.install(ForwardEntry("C", "D", ["A", "D", "C"], version=2))
        assert t.lookup("C").next_hop == "D"

    def test_rejects_hop_limit_violation(self):
        t = ForwardingTable("A", hop_limit=2)
        assert not t.install(ForwardEntry("Z", "B", ["A", "B", "C", "D", "Z"], version=1))
        assert t.lookup("Z") is None

    def test_rejects_looping_path(self):
        t = ForwardingTable("A")
        assert not t.install(ForwardEntry("C", "A", ["A", "B", "A", "C"], version=1))

    def test_link_name_is_order_independent(self):
        assert link_name("A", "B") == link_name("B", "A") == "A-B"


# ---------------------------------------------------------------------------
# Real forwarding across sockets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_is_forwarded_end_to_end():
    """A -> B -> C over two real TCP connections."""
    ports = free_ports(3)
    key = generate_key()
    delivered = asyncio.Event()
    seen = {}

    a = ForwardingNode("A", port=ports[0], shared_key=key)
    b = ForwardingNode("B", port=ports[1], shared_key=key)
    c = ForwardingNode("C", port=ports[2], shared_key=key,
                       on_event=lambda t, p: (seen.update(p),
                                               delivered.set()
                                               if t == "transfer.chunk_delivered" else None))

    for node, nbr, port in ((a, "B", ports[1]), (b, "A", ports[0])):
        node.add_neighbour(nbr, "127.0.0.1", port)
    b.add_neighbour("C", "127.0.0.1", ports[2])
    c.add_neighbour("B", "127.0.0.1", ports[1])

    edges = [("A", "B"), ("B", "C")]
    for node in (a, b, c):
        node.install_topology(["A", "B", "C"], edges)
        await node.start()

    try:
        a.recompute_routes()
        b.recompute_routes()
        route = a.table.lookup("C")
        assert route is not None, "A must have a route to C"
        assert route.next_hop == "B"

        ok = await a.send_data("127.0.0.1", ports[0], destination="C",
                               body=b"payload-for-c")
        assert ok
        await asyncio.wait_for(delivered.wait(), timeout=5.0)
        assert seen.get("destination") == "C"
        assert b.forwarded >= 1, "the middle node must have forwarded the message"
    finally:
        for node in (a, b, c):
            await node.stop()


@pytest.mark.asyncio
async def test_alternate_route_used_after_link_failure():
    """Ring A-B-C-D: cut A-B, A must still reach B via D and C."""
    ports = free_ports(4)
    a = ForwardingNode("A", port=ports[0])
    b = ForwardingNode("B", port=ports[1])
    c = ForwardingNode("C", port=ports[2])
    d = ForwardingNode("D", port=ports[3])

    pairs = [("A", "B"), ("B", "C"), ("C", "D"), ("D", "A")]
    by_id = {"A": a, "B": b, "C": c, "D": d}
    for x, y in pairs:
        by_id[x].add_neighbour(y, "127.0.0.1", ports["ABCD".index(y)])
        by_id[y].add_neighbour(x, "127.0.0.1", ports["ABCD".index(x)])
    for node in by_id.values():
        node.install_topology(["A", "B", "C", "D"], pairs)
        await node.start()

    try:
        a.recompute_routes()
        assert a.table.lookup("B").next_hop == "B", "direct route initially"

        a.router.mark_failed("A-B")
        routes = a.recompute_routes()
        assert routes["B"]["status"] == "OK"
        assert routes["B"]["path"] == ["A", "D", "C", "B"], (
            f"expected the long way round, got {routes['B']['path']}"
        )
        assert routes["B"]["version"] > 0, "route version must advance on failure"
    finally:
        for node in by_id.values():
            await node.stop()


@pytest.mark.asyncio
async def test_recovery_is_proven_by_verified_delivery():
    """
    The acceptance rule: recovery is not reported until the destination actually
    receives the data.

    Ring A-B-C-D. Cut A-B. A must still reach D's far side, and the only way to
    know that for certain is to send a message and receive an end-to-end ACK from
    the destination node itself.
    """
    ports = free_ports(4)
    by_id = {n: ForwardingNode(n, port=p) for n, p in zip("ABCD", ports)}
    pairs = [("A", "B"), ("B", "C"), ("C", "D"), ("D", "A")]
    for x, y in pairs:
        by_id[x].add_neighbour(y, "127.0.0.1", ports["ABCD".index(y)])
        by_id[y].add_neighbour(x, "127.0.0.1", ports["ABCD".index(x)])
    for node in by_id.values():
        node.install_topology(list("ABCD"), pairs)
        await node.start()

    try:
        for node in by_id.values():
            node.recompute_routes()

        ok_before = await by_id["A"].send_data("127.0.0.1", ports[0],
                                               destination="D", body=b"before",
                                               timeout=3.0)
        assert ok_before is True, "baseline delivery must be verified by an ACK"
        assert by_id["D"].delivered >= 1

        # Cut the A-B link on every node and recompute.
        for node in by_id.values():
            node.router.mark_failed("A-B")
            node.recompute_routes()

        routes = by_id["A"].recompute_routes()
        assert routes["B"]["path"] == ["A", "D", "C", "B"], (
            "A must route to B the long way round once A-B is cut"
        )

        ok_after = await by_id["A"].send_data("127.0.0.1", ports[0],
                                              destination="D", body=b"after",
                                              timeout=3.0)
        assert ok_after is True, (
            "delivery must still be confirmed end to end after recovery routing"
        )
        assert by_id["D"].delivered >= 2, "the destination really did receive it"
        assert by_id["A"].acks_accepted >= 2
        assert by_id["A"].acks_timed_out == 0
    finally:
        for node in by_id.values():
            await node.stop()


@pytest.mark.asyncio
async def test_unreachable_is_reported_not_faked():
    """With no alternate path, the destination must be UNREACHABLE."""
    ports = free_ports(2)
    a = ForwardingNode("A", port=ports[0])
    b = ForwardingNode("B", port=ports[1])
    a.add_neighbour("B", "127.0.0.1", ports[1])
    b.add_neighbour("A", "127.0.0.1", ports[0])
    for node in (a, b):
        node.install_topology(["A", "B"], [("A", "B")])

    await a.start()
    await b.start()
    try:
        a.recompute_routes()
        assert a.table.lookup("B") is not None

        a.router.mark_failed("A-B")
        routes = a.recompute_routes()
        assert routes["B"]["status"] == "UNREACHABLE"
        assert routes["B"]["reason"]
        assert a.table.lookup("B") is None, "stale route must be invalidated"

        # Sending must fail honestly rather than claim success.
        ok = await a.send_data("127.0.0.1", ports[0], destination="B", body=b"x",
                               timeout=2.0)
        assert ok is False, (
            "an unreachable destination must never be reported as delivered"
        )
        assert a.acks_timed_out >= 1, "the missing ACK must be recorded, not ignored"
        assert a.dropped_no_route >= 1, "delivery must be dropped, not faked"
    finally:
        await a.stop()
        await b.stop()


@pytest.mark.asyncio
async def test_forged_message_is_rejected():
    """A frame with a bad MAC must not be processed."""
    ports = free_ports(2)
    key = generate_key()
    rejected = asyncio.Event()

    victim = ForwardingNode(
        "V", port=ports[0], shared_key=key,
        on_event=lambda t, p: rejected.set() if t == "security.auth_failure" else None,
    )
    await victim.start()
    try:
        forged = Message(4, {"src": "X", "dst": "V", "body": "evil"})
        import json as _json
        from netrescue.forwarding.node import HEADER, MAGIC, VERSION

        body = _json.dumps(forged.payload, sort_keys=True,
                           separators=(",", ":")).encode()
        raw = HEADER.pack(MAGIC, VERSION, 4, 0, len(body)) + body

        reader, writer = await asyncio.open_connection("127.0.0.1", ports[0])
        writer.write(raw)
        await writer.drain()
        await asyncio.wait_for(rejected.wait(), timeout=5.0)
        writer.close()

        assert victim.dropped_invalid >= 1
        assert victim.forwarded == 0
    finally:
        await victim.stop()

@pytest.mark.asyncio
async def test_non_netrescue_bytes_are_recorded_and_do_not_disturb_delivery():
    """Garbage on a node port is counted with its peer and bytes, and real traffic still flows."""
    ports = free_ports(2)
    key = generate_key()
    events = []
    delivered = asyncio.Event()

    a = ForwardingNode("A", port=ports[0], shared_key=key)
    b = ForwardingNode("B", port=ports[1], shared_key=key,
                       on_event=lambda t, p: (events.append((t, p)),
                                               delivered.set() if t == "transfer.chunk_delivered" else None))
    a.add_neighbour("B", "127.0.0.1", ports[1])
    b.add_neighbour("A", "127.0.0.1", ports[0])
    for node in (a, b):
        node.install_topology(["A", "B"], [("A", "B")])
        await node.start()
    try:
        # An HTTP client wandering onto the port: not a NetRescue frame.
        reader, writer = await asyncio.open_connection("127.0.0.1", ports[1])
        writer.write(b"GET / HTTP/1.1\r\n\r\n")
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), timeout=3.0) == b""  # node closes it
        writer.close()

        assert b.dropped_invalid == 1
        bad = [p for t, p in events if p.get("reason") == "bad_framing"]
        assert len(bad) == 1 and bad[0]["first_bytes"].startswith(b"GET".hex())
        assert bad[0]["peer"].startswith("('127.0.0.1'")

        # The node is unaffected: a genuine message is still delivered.
        a.recompute_routes()
        assert await a.send_data("127.0.0.1", ports[0], destination="B", body=b"still-works")
        await asyncio.wait_for(delivered.wait(), timeout=5.0)
    finally:
        for node in (a, b):
            await node.stop()
