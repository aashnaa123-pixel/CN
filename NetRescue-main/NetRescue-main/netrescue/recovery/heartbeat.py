"""
netrescue/recovery/heartbeat.py

UDP heartbeat sender/receiver with TCP fallback, HMAC authentication,
and sliding-window per-link miss tracking (PRD §4.3).

Security:
- Each heartbeat is HMAC-SHA256 authenticated.
- Sequence numbers and a freshness window prevent replay attacks (PRD §9).
- No private keys or shared secrets are logged.

Usage::

    # Sender side
    sender = HeartbeatSender(link_id="A-B", local_id="A", peer_host="127.0.0.1",
                             peer_port=9200, shared_key=b"secret", interval_s=1.0)
    asyncio.create_task(sender.run())

    # Receiver side
    receiver = HeartbeatReceiver(link_id="A-B", local_id="B", host="0.0.0.0",
                                 port=9200, shared_key=b"secret",
                                 on_heartbeat=my_callback)
    asyncio.create_task(receiver.run())
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from netrescue.security.hmac_auth import ReplayGuard, sign, verify

log = logging.getLogger(__name__)

# Freshness window: reject heartbeats older than this (seconds)
FRESHNESS_WINDOW_S = 30

# Maximum tracked sequence numbers for replay detection
MAX_SEEN_SEQS = 1024


# ---------------------------------------------------------------------------
# Heartbeat message
# ---------------------------------------------------------------------------


def _make_heartbeat(link_id: str, sender_id: str, seq: int, shared_key: bytes) -> bytes:
    """
    Build a HMAC-authenticated heartbeat message.

    Wire format (JSON), identical to every other signed control message::

        { "link_id": ..., "sender_id": ..., "seq": ..., "ts": ..., "mac": <hex> }

    Signing delegates to netrescue.security.hmac_auth so the canonical byte form
    is defined in exactly one place — a second, subtly different serialisation
    here would silently break every verification.
    """
    payload = sign(
        {"link_id": link_id, "sender_id": sender_id, "seq": seq, "ts": time.time()},
        shared_key,
    )
    return json.dumps(payload).encode()


def _verify_heartbeat(data: bytes, shared_key: bytes, freshness_window_s: float = FRESHNESS_WINDOW_S) -> dict | None:
    """
    Verify and parse a heartbeat.

    Returns the parsed dict when the MAC is valid, or None when it is forged.
    Freshness and replay are enforced separately by the receiver's ReplayGuard,
    which needs state carried across calls.
    Never logs the shared key.
    """
    try:
        msg = json.loads(data)
    except Exception as exc:
        log.debug("[heartbeat] Parse error: %s", exc)
        return None

    if not verify(msg, shared_key):
        log.warning("[heartbeat] HMAC verification failed — message rejected.")
        return None
    return msg


# ---------------------------------------------------------------------------
# Sliding window miss tracker
# ---------------------------------------------------------------------------


class SlidingWindow:
    """
    Per-link sliding window (ring buffer) tracking heartbeat hits/misses.

    Provides consecutive miss count and consecutive success count,
    required by the hysteresis state machine.
    """

    def __init__(self, size: int = 10):
        self._window: deque[bool] = deque(maxlen=size)
        self._last_seq: int | None = None

    def record(self, received: bool) -> None:
        """Record a heartbeat slot as received (True) or missed (False)."""
        self._window.append(received)

    @property
    def consecutive_misses(self) -> int:
        """Count consecutive misses from the most recent slot."""
        count = 0
        for received in reversed(self._window):
            if not received:
                count += 1
            else:
                break
        return count

    @property
    def consecutive_successes(self) -> int:
        """Count consecutive received heartbeats from the most recent slot."""
        count = 0
        for received in reversed(self._window):
            if received:
                count += 1
            else:
                break
        return count


# ---------------------------------------------------------------------------
# Heartbeat Sender
# ---------------------------------------------------------------------------


class HeartbeatSender:
    """
    Periodic UDP heartbeat sender with TCP fallback (PRD §4.3).
    """

    def __init__(
        self,
        link_id: str,
        local_id: str,
        peer_host: str,
        peer_port: int,
        shared_key: bytes,
        interval_s: float = 1.0,
    ):
        self.link_id = link_id
        self.local_id = local_id
        self.peer_host = peer_host
        self.peer_port = peer_port
        self.shared_key = shared_key
        self.interval_s = interval_s
        self._seq = 0
        self._running = False

    async def run(self) -> None:
        """Send heartbeats indefinitely until stopped."""
        self._running = True
        log.info("[%s] Heartbeat sender → %s:%d (interval=%.1fs)", self.link_id, self.peer_host, self.peer_port, self.interval_s)
        loop = asyncio.get_running_loop()

        while self._running:
            self._seq += 1
            msg = _make_heartbeat(self.link_id, self.local_id, self._seq, self.shared_key)
            await self._send_udp(loop, msg)
            await asyncio.sleep(self.interval_s)

    async def _send_udp(self, loop: asyncio.AbstractEventLoop, msg: bytes) -> None:
        try:
            transport, _ = await loop.create_datagram_endpoint(
                asyncio.DatagramProtocol,
                remote_addr=(self.peer_host, self.peer_port),
            )
            transport.sendto(msg)
            transport.close()
        except Exception as exc:
            log.debug("[%s] UDP send failed: %s — trying TCP fallback.", self.link_id, exc)
            await self._send_tcp(msg)

    async def _send_tcp(self, msg: bytes) -> None:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.peer_host, self.peer_port), timeout=2.0
            )
            writer.write(msg)
            await writer.drain()
            writer.close()
        except Exception as exc:
            log.warning("[%s] TCP fallback also failed: %s", self.link_id, exc)

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# Heartbeat Receiver
# ---------------------------------------------------------------------------


HeartbeatCallback = Callable[[str, bool, int], None]
"""Signature: on_heartbeat(link_id: str, received: bool, consecutive_misses: int) -> None"""


class HeartbeatReceiver:
    """
    UDP heartbeat receiver with HMAC verification and replay protection.
    Calls on_heartbeat callback on every slot tick.
    """

    def __init__(
        self,
        link_id: str,
        local_id: str,
        host: str,
        port: int,
        shared_key: bytes,
        interval_s: float = 1.0,
        window_size: int = 10,
        on_heartbeat: HeartbeatCallback | None = None,
    ):
        self.link_id = link_id
        self.local_id = local_id
        self.host = host
        self.port = port
        self.shared_key = shared_key
        self.interval_s = interval_s
        self.window = SlidingWindow(size=window_size)
        self.on_heartbeat = on_heartbeat
        # Shared replay/freshness guard — same logic as every other control
        # message, so a heartbeat cannot bypass the checks other traffic gets.
        self._guard = ReplayGuard(
            freshness_window_s=FRESHNESS_WINDOW_S, capacity=MAX_SEEN_SEQS
        )
        self._last_received_ts: float | None = None
        self._running = False

    class _Protocol(asyncio.DatagramProtocol):
        def __init__(self, receiver: "HeartbeatReceiver"):
            self._rx = receiver

        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            self._rx._on_datagram(data)

    def _on_datagram(self, data: bytes) -> None:
        msg = _verify_heartbeat(data, self.shared_key)
        if msg is None:
            return  # forged MAC

        accepted, reason = self._guard.check(msg)
        if not accepted:
            log.warning("[%s] Heartbeat rejected: %s", self.link_id, reason)
            return
        self._guard.accept(int(msg["seq"]))

        self._last_received_ts = time.monotonic()
        log.debug("[%s] Heartbeat received seq=%s from %s", self.link_id, msg.get("seq"), msg.get("sender_id"))

    async def run(self) -> None:
        """Receive datagrams and drive the sliding window at interval_s ticks."""
        self._running = True
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: self._Protocol(self),
            local_addr=(self.host, self.port),
        )
        log.info("[%s] Heartbeat receiver on %s:%d", self.link_id, self.host, self.port)

        try:
            while self._running:
                await asyncio.sleep(self.interval_s)
                # A heartbeat "arrived" in this window if last_received_ts is fresh
                arrived = (
                    self._last_received_ts is not None
                    and (time.monotonic() - self._last_received_ts) < self.interval_s * 1.5
                )
                self._last_received_ts = None  # reset for next window
                self.window.record(arrived)

                if self.on_heartbeat:
                    self.on_heartbeat(
                        self.link_id, arrived, self.window.consecutive_misses
                    )
        finally:
            transport.close()

    def stop(self) -> None:
        self._running = False
