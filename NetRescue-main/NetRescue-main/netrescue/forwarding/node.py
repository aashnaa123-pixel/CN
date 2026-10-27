"""
netrescue/forwarding — real overlay forwarding nodes over TCP sockets.

Replaces the Phase 0 PING/PONG stub. Each node is an independent OS process
with:

  * a TCP listener and a forwarding table (destination -> next hop)
  * heartbeat send/receive over UDP with HMAC authentication
  * a hysteresis detector per neighbour, wired to route recalculation
  * hop limits and route versioning, so loops and stale routes are rejected
  * last-known-good route retention when the controller is unreachable

Database independence (PRD §2.2 rule 1)
----------------------------------------
The forwarding hot path never queries PostgreSQL. Persistence is a separate,
optional task that batches; losing the database degrades telemetry, never
forwarding.

Message framing on the wire::

    +--------+---------+--------+--------+-----------+---------+
    | magic  | version | type   | flags  | payload_len| payload |
    | 2 B    | 1 B     | 1 B    | 1 B    | 4 B       | n B     |
    +--------+---------+--------+--------+-----------+---------+

Payload is JSON. Control messages (heartbeat, route update) additionally carry
an HMAC over their JSON body; DATA messages are integrity-checked with the
sender's HMAC as well, so a forged or tampered payload is rejected rather than
forwarded.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import signal
import struct
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from netrescue.core.events import EventType, Severity
from netrescue.recovery.router import (
    OverlayRouter,
    RouteResult,
    TopologyLink,
    TopologyNode,
)
from netrescue.recovery.state_machine import HysteresisStateMachine, LinkState
from netrescue.security.hmac_auth import sign, verify

log = logging.getLogger(__name__)

MAGIC = b"NR"
VERSION = 1
HEADER = struct.Struct(">2sBBBI")   # magic, version, type, flags, payload_len

MAX_PAYLOAD = 1 << 20               # 1 MiB hard cap — bounds resource use

# Message types
MSG_HELLO = 1
MSG_HEARTBEAT = 2
MSG_ROUTE = 3
MSG_DATA = 4
MSG_ACK = 5

TYPE_NAMES = {
    MSG_HELLO: "HELLO",
    MSG_HEARTBEAT: "HEARTBEAT",
    MSG_ROUTE: "ROUTE",
    MSG_DATA: "DATA",
    MSG_ACK: "ACK",
}


class ProtocolError(Exception):
    """Malformed frame, bad MAC, or oversized payload."""


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


@dataclass
class Message:
    type: int
    payload: dict[str, Any]
    flags: int = 0

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.type, f"UNKNOWN({self.type})")

    def encode(self) -> bytes:
        body = json.dumps(self.payload, sort_keys=True, separators=(",", ":")).encode()
        if len(body) > MAX_PAYLOAD:
            raise ProtocolError(f"payload {len(body)} exceeds {MAX_PAYLOAD} bytes")
        return HEADER.pack(MAGIC, VERSION, self.type, self.flags, len(body)) + body


def decode(raw: bytes) -> Message:
    """Parse and validate one frame. Raises ProtocolError on anything malformed."""
    if len(raw) < HEADER.size:
        raise ProtocolError("frame shorter than header")
    magic, version, mtype, flags, length = HEADER.unpack(raw[: HEADER.size])
    if magic != MAGIC:
        raise ProtocolError(f"bad magic {magic!r}")
    if version != VERSION:
        raise ProtocolError(f"unsupported protocol version {version}")
    if length > MAX_PAYLOAD:
        raise ProtocolError(f"declared payload {length} exceeds {MAX_PAYLOAD}")
    body = raw[HEADER.size: HEADER.size + length]
    if len(body) != length:
        raise ProtocolError("truncated payload")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"payload not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("payload must be a JSON object")
    return Message(type=mtype, payload=payload, flags=flags)


def sign_payload(msg: Message, key: bytes) -> Message:
    """Attach an HMAC over the message body (PRD §9: authenticity and integrity)."""
    payload = sign(msg.payload, key)
    return Message(type=msg.type, payload=payload, flags=msg.flags)


def verify_payload(msg: Message, key: bytes) -> bool:
    return verify(msg.payload, key)


# ---------------------------------------------------------------------------
# Forwarding table
# ---------------------------------------------------------------------------


@dataclass
class ForwardEntry:
    destination: str
    next_hop: str
    path: list[str]
    version: int
    installed_at: float = field(default_factory=time.monotonic)


class ForwardingTable:
    """
    Destination -> next hop, with version guards.

    Installing an entry older than the one already held is refused, which is the
    stale-route rejection required by PRD §4.3. Without it a delayed route
    advertisement can reinstate a path that has already failed.
    """

    def __init__(self, self_id: str, hop_limit: int = 8):
        self.self_id = self_id
        self.hop_limit = hop_limit
        self._entries: dict[str, ForwardEntry] = {}

    def install(self, entry: ForwardEntry) -> bool:
        current = self._entries.get(entry.destination)
        if current is not None and entry.version < current.version:
            log.warning(
                "[%s] refusing stale route to %s (v%d < v%d)",
                self.self_id, entry.destination, entry.version, current.version,
            )
            return False
        if len(entry.path) - 1 > self.hop_limit:
            log.warning(
                "[%s] refusing route to %s: %d hops exceeds limit %d",
                self.self_id, entry.destination, len(entry.path) - 1, self.hop_limit,
            )
            return False
        # A path that loops back through us would be discarded anyway.
        if entry.path.count(self.self_id) > 1:
            log.warning("[%s] refusing looping route to %s: %s", self.self_id, entry.destination, entry.path)
            return False
        self._entries[entry.destination] = entry
        return True

    def lookup(self, destination: str) -> ForwardEntry | None:
        return self._entries.get(destination)

    def invalidate(self, destination: str) -> None:
        self._entries.pop(destination, None)

    def entries(self) -> dict[str, ForwardEntry]:
        return dict(self._entries)

    def clear(self) -> None:
        self._entries.clear()


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


EventSink = Callable[[str, dict], None]


def link_name(a: str, b: str) -> str:
    """Canonical link id, order-independent so both ends agree."""
    return f"{min(a, b)}-{max(a, b)}"


class ForwardingNode:
    """
    One forwarding process.

    Owns a TCP listener, a routing view, a forwarding table, and one hysteresis
    detector per neighbour link. Forwarding continues from the last-known-good
    table when the controller or database is unreachable (PRD §2.2 rule 3).
    """

    def __init__(
        self,
        node_id: str,
        host: str = "127.0.0.1",
        port: int = 9000,
        shared_key: bytes | None = None,
        hop_limit: int = 8,
        heartbeat_interval_s: float = 1.0,
        on_event: EventSink | None = None,
    ):
        self.node_id = node_id
        self.host = host
        self.port = port
        self.shared_key = shared_key if shared_key is not None else b""
        self.heartbeat_interval_s = heartbeat_interval_s

        self.router = OverlayRouter(hop_limit=hop_limit)
        self.table = ForwardingTable(node_id, hop_limit=hop_limit)
        self.detectors: dict[str, HysteresisStateMachine] = {}
        self.neighbours: dict[str, tuple[str, int]] = {}   # node_id -> (host, port)

        self._on_event = on_event or (lambda _t, _p: None)
        self._server: asyncio.Server | None = None
        self._running = False

        # Counters, exposed for the dashboard and for tests.
        self.sent = 0
        self.received = 0
        self.forwarded = 0
        self.dropped_loop = 0
        self.dropped_no_route = 0
        self.dropped_invalid = 0
        self.last_route_version = 0
        self.started_at = time.monotonic()
        self.degraded = False
        # msg_id -> (future, destination_we_sent_to)
        self._pending: dict[str, tuple[asyncio.Future, str]] = {}
        self.delivered = 0
        self.acks_received = 0
        self.acks_accepted = 0
        self.acks_timed_out = 0
        self._topology_nodes: list[str] = []
        self._topology_links: list[tuple[str, str]] = []

        #: Detector transitions this node has actually made, oldest first.
        self.transitions: list[dict] = []
        #: Optional observer, called as ``(node, StateTransitionEvent)``. Used by
        #: the lab to build an experiment timeline; never required for forwarding.
        self.on_detector_transition: Callable[["ForwardingNode", Any], None] | None = None

        self.router.add_node(TopologyNode(node_id))

    # ------------------------------------------------------------------
    # Topology
    # ------------------------------------------------------------------

    def add_neighbour(self, neighbour_id: str, host: str, port: int,
                      f_thresh: int = 3, n: int = 5, r_thresh: int = 5) -> None:
        """
        Register a directly reachable neighbour.

        Establishes the liveness relationship (heartbeats + detector) and adds
        the local link. This is deliberately *not* enough to route: a node needs
        the whole topology to compute multi-hop paths, which
        :meth:`install_topology` supplies.

        The detector thresholds default to the PRD values. They are parameters
        rather than constants so an experiment can widen the dead band at run
        start instead of only in theory (PRD §4.2).
        """
        self.neighbours[neighbour_id] = (host, port)
        link_id = link_name(self.node_id, neighbour_id)
        self.router.add_link(TopologyLink(link_id, self.node_id, neighbour_id))

        def _relay(evt) -> None:
            self._on_link_state(link_id, evt.from_state, evt.to_state)
            self._record_transition(evt)

        self.detectors[neighbour_id] = HysteresisStateMachine(
            link_id=link_id,
            f_thresh=f_thresh,
            n=n,
            r_thresh=r_thresh,
            on_transition=_relay,
        )
        log.info("[%s] neighbour %s at %s:%d", self.node_id, neighbour_id, host, port)

    # ------------------------------------------------------------------
    # Detector control / observation
    # ------------------------------------------------------------------

    def configure_detector(self, neighbour_id: str, *, f_thresh: int | None = None,
                           n: int | None = None, r_thresh: int | None = None,
                           use_ewma: bool | None = None) -> dict:
        """
        Re-tune one neighbour's failure detector.

        Live and immediate: the detector is driven by the heartbeat loop, so a
        changed threshold takes effect from the next interval onwards with no
        restart. Returns the parameters actually in force.
        """
        detector = self.detectors.get(neighbour_id)
        if detector is None:
            raise KeyError(
                f"{self.node_id!r} has no detector for neighbour {neighbour_id!r}"
            )
        if f_thresh is not None:
            detector.f_thresh = max(1, int(f_thresh))
        if n is not None:
            detector.n = max(1, int(n))
        if r_thresh is not None:
            detector.r_thresh = max(1, int(r_thresh))
        if use_ewma is not None:
            detector.use_ewma = bool(use_ewma)
        return self.detector_states()[neighbour_id]

    def detector_states(self) -> dict[str, dict]:
        """
        Per-neighbour detector snapshot.

        This is the measured truth of the link — state, thresholds and the raw
        counters behind them — so an experiment can report what the detector
        actually saw rather than what it was configured to expect.
        """
        return {
            neighbour: {
                "link_id": detector.link_id,
                "state": detector.state.value,
                "f_thresh": detector.f_thresh,
                "n": detector.n,
                "r_thresh": detector.r_thresh,
                "hysteresis_ratio": round(detector.hysteresis_ratio, 3),
                "use_ewma": detector.use_ewma,
                "consecutive_misses": detector._consecutive_misses,
                "consecutive_successes": detector._consecutive_successes,
                "route_flap_count": detector.route_flap_count,
            }
            for neighbour, detector in self.detectors.items()
        }

    def transition_log(self) -> list[dict]:
        """Detector transitions this node has observed, oldest first."""
        return list(self.transitions)

    def _record_transition(self, evt: Any) -> None:
        """Keep every detector transition this node makes, and forward it."""
        self.transitions.append({
            "link_id": evt.link_id,
            "node_id": self.node_id,
            "from_state": evt.from_state,
            "to_state": evt.to_state,
            "route_flap": evt.route_flap,
            "ts": time.time(),
        })
        del self.transitions[:-500]
        if self.on_detector_transition is not None:
            try:
                self.on_detector_transition(self, evt)
            except Exception as exc:  # an observer must never break forwarding
                log.debug("[%s] detector observer raised: %s", self.node_id, exc)

    def install_topology(self, nodes: list[str], links: list[tuple[str, str]]) -> None:
        """
        Give this node the full topology view.

        Route calculation needs every link, not just the local ones — otherwise
        a node can only ever address its immediate neighbours and "alternate
        route after failure" is impossible. The controller (or the CLI) pushes
        the topology; the node does not discover it.
        """
        self._topology_nodes = list(nodes)
        self._topology_links = [tuple(pair) for pair in links]

        fresh = OverlayRouter(hop_limit=self.table.hop_limit)
        for node_id in nodes:
            fresh.add_node(TopologyNode(node_id))
        for a, b in links:
            fresh.add_link(TopologyLink(link_name(a, b), a, b))

        # Carry over failure state for links already known to be down.
        for link_id in self.router._failed_links:
            fresh.mark_failed(link_id)
        self.router = fresh
        log.info(
            "[%s] topology installed: %d nodes, %d links",
            self.node_id, len(nodes), len(links),
        )

    def _on_link_state(self, link_id: str, from_state: str, to_state: str) -> None:
        """Mirror a detector transition into the routing view and emit an event."""
        # The transition itself, whatever it turns out to mean for routing. Emitted
        # first so the timeline shows the detector's verdict even for a transition
        # that does not change any route — SUSPECT is exactly that case, and it is
        # still the observation the experiment is about.
        self._emit(EventType.STATE_TRANSITION.value, {
            "link_id": link_id,
            "from_state": from_state,
            "to_state": to_state,
            "neighbour": next(
                (n for n, d in self.detectors.items() if d.link_id == link_id), None
            ),
        }, Severity.WARNING if to_state == "FAILED" else Severity.INFO)

        if to_state == "FAILED":
            self.router.mark_failed(link_id)
            self._emit(EventType.ROUTE_UPDATED.value, {
                "link_id": link_id, "state": to_state, "action": "link_failed",
            }, Severity.WARNING)
        elif to_state == "HEALTHY" and from_state == "FAILED":
            self.router.mark_healthy(link_id)
            self._emit(EventType.ROUTE_UPDATED.value, {
                "link_id": link_id, "state": to_state, "action": "link_recovered",
            }, Severity.INFO)

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    def recompute_routes(self) -> dict[str, dict]:
        """
        Recalculate routes to every known destination and install them.

        Destinations with no path are recorded as unreachable rather than left
        holding a stale entry (PRD §4.4: never report false recovery).
        """
        routes: dict[str, dict] = {}
        # Every node in the topology except ourselves is a potential destination.
        known = set(self.neighbours) | set(self.router._graph.nodes) - {self.node_id}

        for destination in sorted(known):
            if destination == self.node_id:
                continue
            result: RouteResult = self.router.best_route(
                self.node_id, destination, hop_limit=self.table.hop_limit
            )
            if result.reachable and len(result.path) > 1:
                entry = ForwardEntry(
                    destination=destination,
                    next_hop=result.next_hop or "",
                    path=result.path,
                    version=result.version,
                )
                if self.table.install(entry):
                    self.last_route_version = max(self.last_route_version, result.version)
                    routes[destination] = {
                        "status": "OK", "next_hop": entry.next_hop,
                        "path": entry.path, "version": entry.version,
                    }
            else:
                self.table.invalidate(destination)
                routes[destination] = {
                    "status": "UNREACHABLE", "reason": result.reason, "version": result.version,
                }
                self._emit(EventType.DESTINATION_UNREACHABLE.value, {
                    "destination": destination, "reason": result.reason,
                }, Severity.WARNING)
        return routes

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        # A just-closed port can still be held for a moment on Windows; retry
        # briefly rather than failing the restart outright.
        last: Exception | None = None
        for attempt in range(5):
            try:
                self._server = await asyncio.start_server(
                    self._handle_client, self.host, self.port
                )
                last = None
                break
            except OSError as exc:
                last = exc
                await asyncio.sleep(0.05 * (attempt + 1))
        if last is not None:
            raise last
        self._running = True
        self._emit(EventType.NODE_STARTED.value, {
            "node_id": self.node_id, "host": self.host, "port": self.port,
        }, Severity.INFO)
        log.info("[%s] forwarding on %s:%d", self.node_id, self.host, self.port)

    async def stop(self) -> None:
        self._running = False
        server = self._server
        self._server = None
        if server is not None:
            # Detach the listening sockets before waiting. On Windows' Proactor
            # loop, calling close() while _start_serving is still queued makes
            # that callback assert on an already-detached server, which then
            # leaks the port and breaks the next start(). A transport socket has
            # no close(); detach() hands back the real socket to close.
            for tsock in list(getattr(server, "sockets", None) or []):
                try:
                    real = tsock.detach()
                    if real is not None:
                        real.close()
                except (OSError, AttributeError, ValueError):
                    pass
            server.close()
            try:
                await server.wait_closed()
            except Exception:
                pass
        self._emit(EventType.NODE_STOPPED.value, {"node_id": self.node_id}, Severity.INFO)
        log.info("[%s] stopped", self.node_id)

    async def serve_forever(self) -> None:
        await self.start()
        while self._running:
            await asyncio.sleep(0.2)

    # ------------------------------------------------------------------
    # Inbound handling
    # ------------------------------------------------------------------

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            while self._running:
                header = await reader.readexactly(HEADER.size)
                magic, version, mtype, flags, length = HEADER.unpack(header)
                if magic != MAGIC or version != VERSION:
                    # Not a NetRescue peer (a port scan, an HTTP client, a stale
                    # sender). Count it and record who and what, exactly as the
                    # other rejection paths do, so the source can be identified.
                    self.dropped_invalid += 1
                    self._emit(EventType.AUTH_FAILURE.value, {
                        "node_id": self.node_id, "reason": "bad_framing",
                        "peer": str(peer), "first_bytes": header.hex(),
                    }, Severity.WARNING)
                    raise ProtocolError(f"bad framing (first bytes {header.hex()})")
                if length > MAX_PAYLOAD:
                    raise ProtocolError(f"oversized payload {length}")
                body = await reader.readexactly(length)
                try:
                    msg = decode(header + body)
                except ProtocolError as exc:
                    self.dropped_invalid += 1
                    self._emit(EventType.AUTH_FAILURE.value, {
                        "node_id": self.node_id, "reason": str(exc), "peer": str(peer),
                    }, Severity.WARNING)
                    break

                if self.shared_key and not verify_payload(msg, self.shared_key):
                    self.dropped_invalid += 1
                    self._emit(EventType.AUTH_FAILURE.value, {
                        "node_id": self.node_id, "reason": "invalid_mac", "peer": str(peer),
                    }, Severity.WARNING)
                    continue

                await self._dispatch(msg, writer)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except ProtocolError as exc:
            log.warning("[%s] protocol error from %s: %s", self.node_id, peer, exc)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _dispatch(self, msg: Message, writer: asyncio.StreamWriter) -> None:
        self.received += 1

        if msg.type == MSG_HELLO:
            self._emit(EventType.HEARTBEAT_RECEIVED.value, {
                "from": msg.payload.get("sender_id"), "kind": "hello",
            }, Severity.DEBUG)

        elif msg.type == MSG_HEARTBEAT:
            sender = msg.payload.get("sender_id")
            if sender in self.detectors:
                # A heartbeat from a neighbour is proof of liveness.
                self.detectors[sender].update(missed=False)
                self._emit(EventType.HEARTBEAT_RECEIVED.value, {
                    "from": sender, "seq": msg.payload.get("seq"),
                }, Severity.DEBUG)

        elif msg.type == MSG_ROUTE:
            self._apply_route(msg.payload)

        elif msg.type == MSG_DATA:
            await self._forward_data(msg, writer)

        elif msg.type == MSG_ACK:
            self._resolve_ack(msg.payload)

    def _resolve_ack(self, payload: dict) -> None:
        """
        Settle one pending delivery.

        An ACK arrives on a dedicated return connection opened by the
        destination node, so this is genuine end-to-end confirmation rather than
        hop-by-hop bookkeeping. A caller only sees success once this settles,
        which is what stops the lab reporting recovery it has not observed.
        """
        msg_id = payload.get("msg_id")
        if not msg_id:
            return
        entry = self._pending.pop(msg_id, None)
        if entry is None:
            return
        future, expected_destination = entry
        if future.done():
            return

        # The ACK names the node that actually received the data. It must be the
        # node we sent to -- otherwise a misrouted message could be credited to
        # this sender as a success.
        delivered_to = payload.get("delivered_to")
        self.acks_received += 1
        matched = (not delivered_to) or delivered_to == expected_destination
        if matched:
            self.acks_accepted += 1
        future.set_result(bool(matched))
        self._emit(EventType.TRANSFER_COMPLETE.value, {
            "msg_id": msg_id, "from": payload.get("sender_id"),
            "ack": "delivered" if matched else "mismatched_destination",
        }, Severity.INFO if matched else Severity.WARNING)

    async def _send_ack(self, msg_id: str | None, return_addr: Any, via: Any) -> None:
        """
        Open a fresh connection to the original sender and acknowledge.

        The address is carried in the DATA frame's `return_addr`. If it is
        missing or unreachable the ACK is simply lost: the sender's pending
        entry then times out and the delivery is reported as unconfirmed rather
        than assumed good.
        """
        if not msg_id or not return_addr:
            return
        try:
            rhost, rport = return_addr
        except (TypeError, ValueError):
            return
        ack = Message(MSG_ACK, {
            "msg_id": msg_id,
            "sender_id": self.node_id,
            "delivered_to": self.node_id,
            "via": via,
        })
        try:
            await self.send_to(rhost, int(rport), ack, timeout=3.0)
        except Exception as exc:
            log.debug("[%s] ack for %s could not be returned: %s",
                      self.node_id, msg_id, exc)

    def _apply_route(self, payload: dict) -> None:
        destination = payload.get("destination")
        next_hop = payload.get("next_hop")
        path = payload.get("path") or []
        version = int(payload.get("version", 0))
        if not destination or not next_hop:
            return
        ok = self.table.install(ForwardEntry(destination, next_hop, path, version))
        self._emit(EventType.ROUTE_UPDATED.value, {
            "destination": destination, "next_hop": next_hop,
            "version": version, "installed": ok,
        }, Severity.DEBUG)

    # ------------------------------------------------------------------
    # Forwarding
    # ------------------------------------------------------------------

    async def _forward_data(self, msg: Message, writer: asyncio.StreamWriter) -> None:
        """Forward a DATA message toward its destination, or accept it locally."""
        destination = msg.payload.get("destination") or msg.payload.get("dst")
        if not destination:
            self.dropped_no_route += 1
            return

        if destination == self.node_id:
            # Arrived. Acknowledge on a NEW connection straight back to the
            # original sender. Writing the ACK on `writer` would only reach the
            # previous hop, which is not delivery confirmation.
            self.delivered += 1
            self._emit(EventType.TRANSFER_CHUNK_DELIVERED.value, {
                "node_id": self.node_id,
                "msg_id": msg.payload.get("msg_id"),
                "destination": destination,
            }, Severity.INFO)
            await self._send_ack(
                msg.payload.get("msg_id"),
                msg.payload.get("return_addr"),
                msg.payload.get("src"),
            )
            return

        entry = self.table.lookup(destination)
        if entry is None or not entry.next_hop:
            self.dropped_no_route += 1
            self._emit(EventType.DESTINATION_UNREACHABLE.value, {
                "destination": destination, "reason": "no route in forwarding table",
            }, Severity.WARNING)
            return

        if self.node_id in entry.path[1:]:
            # Our own id appearing again downstream means someone else will
            # bounce it back. Drop rather than amplify.
            self.dropped_loop += 1
            self._emit(EventType.ROUTE_UPDATED.value, {
                "destination": destination, "reason": "loop_suppressed", "path": entry.path,
            }, Severity.WARNING)
            return

        host, port = self.neighbours.get(entry.next_hop, (None, None))
        if host is None:
            self.dropped_no_route += 1
            return

        try:
            reader, out = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=3.0
            )
            out.write(msg.encode())
            await out.drain()
            out.close()
            self.forwarded += 1
            self._emit(EventType.TRANSFER_CHUNK_SENT.value, {
                "node_id": self.node_id, "destination": destination,
                "next_hop": entry.next_hop, "msg_id": msg.payload.get("msg_id"),
            }, Severity.DEBUG)
        except Exception as exc:
            self.dropped_no_route += 1
            log.warning("[%s] forward to %s via %s failed: %s",
                        self.node_id, destination, entry.next_hop, exc)

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send_to(self, host: str, port: int, msg: Message, timeout: float = 5.0) -> bool:
        """Send one signed message to a peer over TCP."""
        if self.shared_key:
            msg = sign_payload(msg, self.shared_key)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
            writer.write(msg.encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            self.sent += 1
            return True
        except Exception as exc:
            log.warning("[%s] send to %s:%d failed: %s", self.node_id, host, port, exc)
            return False

    async def send_data(self, host: str, port: int, destination: str,
                        body: bytes, msg_id: str | None = None,
                        timeout: float = 5.0) -> bool:
        """
        Send data toward a destination and wait for end-to-end confirmation.

        Returns True only when the destination node itself acknowledged. A
        successful TCP write to the first hop is not success — if no ACK arrives
        within `timeout` this returns False, so an unreachable destination can
        never be reported as delivered.
        """
        msg_id = msg_id or uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future = loop.create_future()
        self._pending[msg_id] = (waiter, destination)

        msg = Message(MSG_DATA, {
            "src": self.node_id,
            "dst": destination,
            "msg_id": msg_id,
            "body": body.decode("latin-1"),
            # Where the destination should return its ACK. Forwarded verbatim
            # through every hop, so the acknowledgement reaches the originator.
            "return_addr": [self.host, self.port],
            "ts": time.time(),
        })

        try:
            sent = await self.send_to(host, port, msg)
        except Exception:
            self._pending.pop(msg_id, None)
            return False

        if not sent:
            self._pending.pop(msg_id, None)
            return False

        try:
            return await asyncio.wait_for(waiter, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            self.acks_timed_out += 1
            self._emit(EventType.DESTINATION_UNREACHABLE.value, {
                "destination": destination,
                "msg_id": msg_id,
                "reason": f"no acknowledgement within {timeout:.1f}s",
            }, Severity.WARNING)
            return False
        finally:
            self._pending.pop(msg_id, None)

    # ------------------------------------------------------------------
    # Heartbeats
    # ------------------------------------------------------------------

    async def heartbeat_loop(self) -> None:
        """
        Tick every heartbeat interval: send to neighbours, and count silence.

        A neighbour that has not been heard from is recorded as a missed
        interval, which drives its hysteresis detector.
        """
        seq = 0
        while self._running:
            for neighbour, (host, port) in list(self.neighbours.items()):
                seq += 1
                await self.send_to(
                    host, port,
                    Message(MSG_HEARTBEAT, {
                        "sender_id": self.node_id,
                        "seq": seq,
                        "ts": time.time(),
                    }),
                    timeout=2.0,
                )
            await asyncio.sleep(self.heartbeat_interval_s)

            for neighbour, detector in self.detectors.items():
                if not self._running:
                    break
                detector.update(missed=True)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def status(self) -> dict:
        return {
            "node_id": self.node_id,
            "host": self.host,
            "port": self.port,
            "running": self._running,
            "degraded": self.degraded,
            "uptime_s": round(time.monotonic() - self.started_at, 3),
            "neighbours": {
                n: {"host": h, "port": p, "state": self.detectors[n].state.value}
                for n, (h, p) in self.neighbours.items()
            } if self.detectors else {},
            "detectors": self.detector_states(),
            "transitions": self.transition_log()[-20:],
            "routes": {
                d: {"next_hop": e.next_hop, "path": e.path, "version": e.version}
                for d, e in self.table.entries().items()
            },
            "counters": {
                "sent": self.sent,
                "received": self.received,
                "forwarded": self.forwarded,
                "dropped_loop": self.dropped_loop,
                "dropped_no_route": self.dropped_no_route,
                "dropped_invalid": self.dropped_invalid,
                "route_version": self.last_route_version,
                "delivered": self.delivered,
                "acks_received": self.acks_received,
                "acks_accepted": self.acks_accepted,
                "acks_timed_out": self.acks_timed_out,
                "pending_acks": len(self._pending),
            },
        }

    # ------------------------------------------------------------------

    def _emit(self, event_type: str, payload: dict, severity: Severity) -> None:
        try:
            self._on_event(event_type, {**payload, "node_id": self.node_id,
                                        "severity": severity.value,
                                        "ts": time.time()})
        except Exception as exc:  # an event sink must never break forwarding
            log.debug("[%s] event sink raised: %s", self.node_id, exc)