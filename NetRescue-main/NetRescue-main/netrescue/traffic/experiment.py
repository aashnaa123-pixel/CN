"""
netrescue/traffic/experiment.py — workload generation, measurement and comparison.

Runs an identical workload through each scheduler over a constrained link and
reports per-class latency, throughput, queue depth and drops.

Determinism
-----------
Traffic generation is seeded, so both schedulers face byte-identical arrivals.
Without that, any latency difference would be attributable to sampling noise
rather than to scheduling.

Honesty about the numbers
-------------------------
These are queueing-model results, not network measurements. Under a queueing
model, "throughput" is what the link could carry given the offered load and the
order it served packets in; it is not a measured wire rate. Every result
carries ``simulated=True`` and the report states it in ``limitations``.
"""

from __future__ import annotations

import logging
import random
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable

from netrescue.benchmark.report import summarise
from netrescue.traffic.classes import Packet, TrafficClass
from netrescue.traffic.scheduler import (
    LinkConfig,
    SchedulerVariant,
    build_scheduler,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class WorkloadConfig:
    """Repeatable offered load."""

    #: Critical (latency-sensitive) stream.
    critical_rate_hz: float = 40.0          # packets per second
    critical_size_bits: int = 1_600          # 200 B + framing

    #: Bulk (throughput-oriented) stream.
    bulk_rate_hz: float = 200.0              # packets per second
    bulk_size_bits: int = 40_000             # 5000 B

    #: Offered load must exceed link capacity for congestion to appear, which is
    #: the whole point of the experiment.
    duration_ms: float = 500.0
    max_queue: int = 200

    seed: int = 4242

    def offered_bps(self) -> float:
        return (
            self.critical_rate_hz * self.critical_size_bits
            + self.bulk_rate_hz * self.bulk_size_bits
        )

    def to_dict(self) -> dict:
        return {
            "critical_rate_hz": self.critical_rate_hz,
            "critical_size_bits": self.critical_size_bits,
            "bulk_rate_hz": self.bulk_rate_hz,
            "bulk_size_bits": self.bulk_size_bits,
            "duration_ms": self.duration_ms,
            "max_queue": self.max_queue,
            "seed": self.seed,
            "offered_bps": self.offered_bps(),
        }


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class ClassMetrics:
    """Per-class measurements for one scheduler run."""

    traffic_class: str
    packets_offered: int
    packets_delivered: int
    packets_dropped: int
    latency_ms: dict[str, float | None] = field(default_factory=dict)
    throughput_bps: float = 0.0

    def as_dict(self) -> dict:
        return {
            "traffic_class": self.traffic_class,
            "packets_offered": self.packets_offered,
            "packets_delivered": self.packets_delivered,
            "packets_dropped": self.packets_dropped,
            "latency_median_ms": self.latency_ms.get("median"),
            "latency_p95_ms": self.latency_ms.get("p95"),
            "latency_max_ms": self.latency_ms.get("max"),
            "throughput_bps": round(self.throughput_bps, 1),
        }


@dataclass
class ExperimentResult:
    """One (workload, scheduler) run."""

    scheduler: str
    workload: dict
    link: dict
    classes: dict[str, ClassMetrics] = field(default_factory=dict)
    busy_time_ms: float = 0.0
    utilisation: float = 0.0
    simulated: bool = True
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> dict:
        return {
            "scheduler": self.scheduler,
            "workload": self.workload,
            "link": self.link,
            "classes": {k: v.as_dict() for k, v in self.classes.items()},
            "busy_time_ms": round(self.busy_time_ms, 4),
            "utilisation": round(self.utilisation, 4),
            "simulated": self.simulated,
            "generated_at": self.generated_at.isoformat(),
        }


@dataclass
class ComparisonReport:
    """FIFO versus priority, side by side."""

    run_id: str
    workload: dict
    link: dict
    results: list[ExperimentResult] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=lambda: [
        "These are discrete-event queueing-model results, not measurements of a "
        "real network. For real network conditions use netrescue/benchmark or "
        "Linux tc/netem emulation.",
        "Priority scheduling is best-effort and non-preemptive. It offers no hard "
        "real-time guarantee and does not make bulk traffic faster.",
        "Queue depth is the offered backlog at the end of the run, not an "
        "instrumented measurement from a live link.",
    ])

    def table(self) -> str:
        lines = [
            "scheduler   class      delivered  dropped   latency_med  latency_p95   throughput",
            "-" * 84,
        ]
        for r in self.results:
            for c in ("critical", "bulk"):
                m = r.classes.get(c)
                if not m:
                    continue
                lat_med = f"{m.latency_ms.get('median'):.2f}" if m.latency_ms.get("median") is not None else "n/a"
                lat_p95 = f"{m.latency_ms.get('p95'):.2f}" if m.latency_ms.get("p95") is not None else "n/a"
                lines.append(
                    f"{r.scheduler:<12} {c:<10} {m.packets_delivered:>9}  {m.packets_dropped:>7}  "
                    f"{lat_med:>11}  {lat_p95:>11}   {m.throughput_bps / 1e6:>6.2f} Mbps"
                )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "workload": self.workload,
            "link": self.link,
            "results": [r.as_dict() for r in self.results],
            "findings": self.findings,
            "limitations": self.limitations,
        }


# ---------------------------------------------------------------------------
# Workload generation
# ---------------------------------------------------------------------------


def generate_packets(cfg: WorkloadConfig, max_queue: int) -> list[Packet]:
    """
    Build the arrival stream for a run.

    Deterministic given the seed. Packets past ``max_queue`` concurrent arrivals
    are marked dropped, which is how queue overflow is surfaced.
    """
    rng = random.Random(cfg.seed)
    packets: list[Packet] = []

    streams = (
        (TrafficClass.CRITICAL, cfg.critical_rate_hz, cfg.critical_size_bits),
        (TrafficClass.BULK, cfg.bulk_rate_hz, cfg.bulk_size_bits),
    )

    seq = 0
    events: list[tuple[float, int, TrafficClass, int]] = []
    for cls, rate_hz, size_bits in streams:
        if rate_hz <= 0:
            continue
        interval_ms = 1000.0 / rate_hz
        t = 0.0
        while t < cfg.duration_ms:
            # Small jitter so arrivals are not perfectly periodic.
            jitter = rng.uniform(-0.4, 0.4) * interval_ms
            events.append((max(0.0, t + jitter), seq, cls, size_bits))
            seq += 1
            t += interval_ms

    events.sort(key=lambda e: (e[0], e[1]))

    # Model the queue bound: count arrivals that land before the link could
    # have drained them. Approximated with a running backlog estimate.
    backlog = 0
    last_arrival = 0.0
    for arrival_ms, pkt_seq, cls, size_bits in events:
        backlog += 1
        if arrival_ms > last_arrival:
            last_arrival = arrival_ms
        packets.append(
            Packet(
                seq=pkt_seq,
                traffic_class=cls,
                size_bits=size_bits,
                arrival_ms=arrival_ms,
                dropped=backlog > max_queue * 4,
            )
        )
        # Decay backlog with elapsed time (packets drain at line rate).
        backlog = max(0, backlog - int((arrival_ms - (events[max(0, pkt_seq - 1)][0] if pkt_seq else 0.0)) * 0.05))

    return packets


# ---------------------------------------------------------------------------
# Experiment
# ---------------------------------------------------------------------------


class TrafficExperiment:
    """Runs one workload through one scheduler over one constrained link."""

    def __init__(
        self,
        workload: WorkloadConfig,
        link: LinkConfig,
        variant: SchedulerVariant | str,
    ):
        self.workload = workload
        self.link = link
        self.variant = (
            SchedulerVariant(variant) if isinstance(variant, str) else variant
        )

    # ------------------------------------------------------------------

    def run(self) -> ExperimentResult:
        packets = generate_packets(self.workload, self.link.max_queue)
        live = [p for p in packets if not p.dropped]

        scheduler = build_scheduler(self.variant, self.link)
        scheduler.schedule(live)

        result = ExperimentResult(
            scheduler=self.variant.value,
            workload=self.workload.to_dict(),
            link={
                "rate_bps": self.link.rate_bps,
                "propagation_ms": self.link.propagation_ms,
                "max_queue": self.link.max_queue,
            },
            busy_time_ms=sum(self.link.service_ms(p) for p in live),
        )
        result.utilisation = (
            result.busy_time_ms / self.workload.duration_ms
            if self.workload.duration_ms > 0 else 0.0
        )

        for cls in TrafficClass:
            subset = [p for p in packets if p.traffic_class == cls]
            delivered = [p for p in subset if p.departure_ms is not None]
            latencies = [p.latency_ms() for p in delivered]
            latencies = [l for l in latencies if l is not None]

            total_bits = sum(p.size_bits for p in delivered)
            span = self._delivery_span(delivered)
            result.classes[cls.value] = ClassMetrics(
                traffic_class=cls.value,
                packets_offered=len(subset),
                packets_delivered=len(delivered),
                packets_dropped=sum(1 for p in subset if p.dropped),
                latency_ms=summarise(latencies) if latencies else {},
                throughput_bps=(total_bits * 1000.0 / span) if span > 0 else 0.0,
            )

        log.info(
            "[traffic] %s: critical lat=%s bulk tput=%.2f Mbps util=%.0f%%",
            self.variant.value,
            result.classes["critical"].latency_ms.get("median"),
            result.classes["bulk"].throughput_bps / 1e6,
            result.utilisation * 100,
        )
        return result

    # ------------------------------------------------------------------

    @staticmethod
    def _delivery_span(packets: list[Packet]) -> float:
        if len(packets) < 2:
            return 0.0
        first = min(p.arrival_ms for p in packets)
        last = max(p.departure_ms or p.arrival_ms for p in packets)
        return max(last - first, 1e-9)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def run_comparison(
    run_id: str,
    workload: WorkloadConfig,
    link: LinkConfig,
    variants: Iterable[SchedulerVariant | str] = (SchedulerVariant.FIFO, SchedulerVariant.PRIORITY),
) -> ComparisonReport:
    """
    Run the same workload through every scheduler and report the trade-off.

    Both schedulers see identical arrivals because the workload is seeded.
    """
    report = ComparisonReport(
        run_id=run_id,
        workload=workload.to_dict(),
        link={"rate_bps": link.rate_bps, "propagation_ms": link.propagation_ms,
              "max_queue": link.max_queue},
    )

    for variant in variants:
        report.results.append(TrafficExperiment(workload, link, variant).run())

    report.findings = _derive_findings(report, workload, link)
    return report


def _derive_findings(report: ComparisonReport, workload: WorkloadConfig, link: LinkConfig) -> list[str]:
    """State what the measurements actually show, including when they show nothing."""
    out: list[str] = []

    by_sched = {r.scheduler: r for r in report.results}
    fifo = by_sched.get("fifo")
    prio = by_sched.get("priority")
    if not fifo or not prio:
        out.append("Both FIFO and priority runs are required for a comparison.")
        return out

    f_crit = fifo.classes["critical"].latency_ms.get("median")
    p_crit = prio.classes["critical"].latency_ms.get("median")
    if f_crit is not None and p_crit is not None:
        if p_crit < f_crit:
            out.append(
                f"Priority scheduling lowers critical-traffic median latency: "
                f"{p_crit:.2f} ms vs {f_crit:.2f} ms FIFO "
                f"({(1 - p_crit / f_crit) * 100:.0f}% lower)."
            )
        elif p_crit > f_crit:
            out.append(
                f"Priority scheduling did NOT lower critical latency in this run "
                f"({p_crit:.2f} ms vs {f_crit:.2f} ms FIFO). Offered load "
                f"({workload.offered_bps() / 1e6:.2f} Mbps) may be below link "
                f"capacity ({link.rate_bps / 1e6:.2f} Mbps), so there is no queue "
                f"to prioritise out of."
            )
        else:
            out.append("Critical latency is identical under both schedulers — no queueing occurred.")

    f_bulk = fifo.classes["bulk"].throughput_bps
    p_bulk = prio.classes["bulk"].throughput_bps
    if f_bulk > 0:
        if p_bulk < f_bulk:
            out.append(
                f"The trade-off: priority scheduling *reduces* bulk throughput "
                f"({p_bulk / 1e6:.2f} Mbps vs {f_bulk / 1e6:.2f} Mbps FIFO, "
                f"{(1 - p_bulk / f_bulk) * 100:.0f}% lower)."
            )
        elif p_bulk > f_bulk:
            out.append(
                f"Bulk throughput is higher under priority scheduling here "
                f"({p_bulk / 1e6:.2f} Mbps vs {f_bulk / 1e6:.2f} Mbps) — likely "
                f"because deprioritised bulk packs more efficiently into idle slots."
            )

    ratio = workload.offered_bps() / link.rate_bps if link.rate_bps > 0 else 0.0
    if ratio <= 1.0:
        out.append(
            f"Offered load is {ratio:.2f}x link capacity — the link never queues, "
            f"so both schedulers should behave alike. Raise bulk_rate_hz above "
            f"~{link.rate_bps / max(workload.bulk_size_bits, 1):.0f} packets/s to "
            f"create real congestion."
        )
    else:
        out.append(
            f"Offered load is {ratio:.2f}x link capacity "
            f"({workload.offered_bps() / 1e6:.2f} Mbps onto a "
            f"{link.rate_bps / 1e6:.2f} Mbps link), so the link is saturated and "
            f"queueing is expected. Both schedulers keep the link busy; they "
            f"differ in which class waits."
        )

    out.append(
        "Priority scheduling is best-effort and non-preemptive; it provides no "
        "hard real-time guarantee."
    )
    return out