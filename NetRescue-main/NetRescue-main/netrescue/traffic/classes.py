"""
netrescue/traffic/classes.py — traffic classes and their scheduling priority.
"""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field


class TrafficClass(str, Enum):
    """
    The two classes Feature B compares.

    CRITICAL carries latency-sensitive control traffic (heartbeats, route
    updates, keepalives). Small packets, tight latency budget.

    BULK carries file/transfer data. Large packets, throughput matters far more
    than latency.
    """

    CRITICAL = "critical"
    BULK = "bulk"


#: Higher number leaves the link sooner.
CLASS_PRIORITY: dict[TrafficClass, int] = {
    TrafficClass.CRITICAL: 0,
    TrafficClass.BULK: 1,
}


@dataclass
class Packet:
    """
    One queued message.

    `size_bits` drives service time; `arrival_ms` is when it reached the link.
    Latency is departure minus arrival, so queueing delay is captured for free.
    """

    seq: int
    traffic_class: TrafficClass
    size_bits: int
    arrival_ms: float
    departure_ms: float | None = None
    dropped: bool = False

    @property
    def service_time_ms(self) -> float:
        """Transmission time on a link of `rate_bps`."""
        return 0.0  # filled in by the scheduler; kept for clarity of intent

    def latency_ms(self) -> float | None:
        if self.departure_ms is None:
            return None
        return self.departure_ms - self.arrival_ms

    def __repr__(self) -> str:
        state = "dropped" if self.dropped else (
            f"dep@{self.departure_ms:.3f}" if self.departure_ms is not None else "queued"
        )
        return f"<Packet {self.seq} {self.traffic_class.value} {self.size_bits}b {state}>"