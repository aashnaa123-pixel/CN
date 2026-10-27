"""
netrescue/traffic/scheduler.py — FIFO and priority link schedulers.

Both schedulers sit in front of one constrained link. They differ only in the
order they choose from the queue; the service model is shared so a comparison
between them is attributable to scheduling alone.

Service model
-------------
A packet occupies the link for ``size_bits / rate_bps``. A packet arriving
while the link is busy waits until the link free time, then is served. This is
the standard one-server queueing model with a finite transmission rate — it is
what produces queueing delay under congestion.
"""

from __future__ import annotations

import heapq
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from netrescue.traffic.classes import CLASS_PRIORITY, Packet, TrafficClass

log = logging.getLogger(__name__)


@dataclass
class LinkConfig:
    """Shared, constrained link."""

    #: Link capacity in bits per second. The default workload offers ~1.6x this,
    #: so congestion is present by default — an uncongested link cannot show a
    #: difference between schedulers.
    rate_bps: float = 5_000_000.0
    #: Fixed propagation delay added to every packet, in milliseconds.
    propagation_ms: float = 1.0
    #: Maximum packets the queue will hold. Beyond this, arrivals are dropped.
    max_queue: int = 200

    def service_ms(self, packet: Packet) -> float:
        return (packet.size_bits / self.rate_bps) * 1000.0


class SchedulerVariant(str, Enum):
    FIFO = "fifo"
    PRIORITY = "priority"


class TokenBucketLink:
    """
    One serialised, rate-limited link.

    Tracks when it next becomes free. Callers ask it to serve packets in a
    chosen order; it applies transmission time and reports the departure time.
    """

    def __init__(self, config: LinkConfig):
        self.config = config
        self.free_at_ms = 0.0
        self.served_bits = 0
        self.dropped = 0

    def service(self, packet: Packet) -> float:
        """
        Occupy the link for this packet, returning its departure time in ms.

        If the link is already free the packet departs on arrival; otherwise it
        queues behind whatever is in flight.
        """
        start = max(self.free_at_ms, packet.arrival_ms)
        duration = self.config.service_ms(packet)
        self.free_at_ms = start + duration
        self.served_bits += packet.size_bits
        packet.departure_ms = self.free_at_ms + self.config.propagation_ms
        return packet.departure_ms


class BaseLinkScheduler:
    """Chooses which queued packet the link serves next."""

    variant: SchedulerVariant

    def __init__(self, link: LinkConfig):
        self.link = TokenBucketLink(link)

    def schedule(self, packets: Iterable[Packet]) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------------

    def _arrival_order(self, packets: Iterable[Packet]) -> list[Packet]:
        return sorted(packets, key=lambda p: p.arrival_ms)


class FifoScheduler(BaseLinkScheduler):
    """
    Baseline: serve strictly in arrival order.

    Ignores traffic class entirely. A burst of bulk traffic queued just before a
    critical packet will delay that critical packet — which is exactly the
    behaviour Feature B's priority scheduler exists to improve.
    """

    variant = SchedulerVariant.FIFO

    def schedule(self, packets: Iterable[Packet]) -> None:
        for packet in self._arrival_order(packets):
            self.link.service(packet)


class PriorityScheduler(BaseLinkScheduler):
    """
    Serve critical traffic before bulk; bulk fills otherwise-idle capacity.

    Ordering is ``(class priority, arrival time)``. Within a class, arrival
    order is preserved, so this is priority-with-FIFO-within-class, not strict
    preemption. Bulk that has already begun transmitting is not interrupted —
    priority scheduling here is best-effort, not preemptive, and offers no hard
    real-time guarantee.
    """

    variant = SchedulerVariant.PRIORITY

    def schedule(self, packets: Iterable[Packet]) -> None:
        ordered = sorted(
            packets,
            key=lambda p: (CLASS_PRIORITY[p.traffic_class], p.arrival_ms),
        )
        for packet in ordered:
            self.link.service(packet)


def build_scheduler(variant: SchedulerVariant | str, link: LinkConfig) -> BaseLinkScheduler:
    """Factory used by the experiment runner and the API layer."""
    if isinstance(variant, str):
        variant = SchedulerVariant(variant)
    if variant == SchedulerVariant.FIFO:
        return FifoScheduler(link)
    if variant == SchedulerVariant.PRIORITY:
        return PriorityScheduler(link)
    raise ValueError(f"unknown scheduler variant {variant!r}")