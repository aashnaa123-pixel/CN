"""
netrescue/benchmark/sweep.py

Hysteresis sweep harness (PRD §6.4, §4.4).

Runs the *same* recovery scenario across a range of hysteresis ratios and
collects ``(recovery_time, route_flap_count)`` pairs, producing the trade-off
curve and Pareto frontier that PRD §4.4 and §11 require.

The comparison is against the naive single-threshold detector in
netrescue/recovery/naive.py, driven by an identical heartbeat-loss pattern, so
the flap reduction is attributable to the dead band alone.

Determinism
-----------
The heartbeat outcome sequence is generated once from a fixed seed and replayed
for every ratio. Without that, each ratio would face a different realisation of
the same link behaviour and the curve would be noise.

Usage::

    from netrescue.benchmark.sweep import SweepConfig, HysteresisSweepHarness

    report = await HysteresisSweepHarness(SweepConfig(run_id="sweep-1")).run()
    for p in report.pareto_frontier:
        print(p)
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Sequence

from netrescue.benchmark.report import MIN_REPETITIONS, summarise
from netrescue.recovery.naive import NaiveThresholdDetector
from netrescue.recovery.state_machine import HysteresisStateMachine

log = logging.getLogger(__name__)

#: PRD §4.4 / §6.4 — the sweep points the PRD specifies.
DEFAULT_RATIOS = [1.0, 1.5, 2.0, 2.5, 3.0]

#: PRD §11 — hysteresis must cut flap count by at least this much at ratio >= 2.0.
FLAP_REDUCTION_TARGET = 0.50
MIN_RATIO_FOR_TARGET = 2.0


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class SweepConfig:
    run_id: str

    #: Keep f_thresh fixed and derive r_thresh = ratio * f_thresh, so the ratio
    #: is the single experimental variable (PRD §4.3).
    f_thresh: int = 3
    ratios: list[float] = field(default_factory=lambda: list(DEFAULT_RATIOS))

    #: Naive detector's single threshold, for the control comparison.
    naive_threshold: int = 3

    #: Heartbeat intervals in the scenario.
    total_intervals: int = 400

    #: Intervals during which the link is down, starting here.
    outage_start: int = 100
    outage_length: int = 60

    #: Probability a healthy interval still drops a heartbeat. This is what
    #: makes the link oscillate around the threshold and expose flapping.
    flap_probability: float = 0.12

    repetitions: int = MIN_REPETITIONS
    seed: int = 20260101

    def workload(self) -> dict[str, Any]:
        return {
            "f_thresh": self.f_thresh,
            "ratios": self.ratios,
            "naive_threshold": self.naive_threshold,
            "total_intervals": self.total_intervals,
            "outage": [self.outage_start, self.outage_length],
            "flap_probability": self.flap_probability,
            "repetitions": self.repetitions,
            "heartbeat_interval_s": 1.0,
        }


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


def build_outcome_sequence(cfg: SweepConfig, seed: int) -> list[bool]:
    """
    Generate one heartbeat outcome sequence: True = heartbeat missed.

    Structure: healthy baseline, a sustained outage, then recovery, with random
    drops sprinkled through the healthy stretches to create oscillation.
    """
    rng = random.Random(seed)
    seq: list[bool] = []
    for i in range(cfg.total_intervals):
        in_outage = cfg.outage_start <= i < cfg.outage_start + cfg.outage_length
        if in_outage:
            seq.append(True)
        else:
            seq.append(rng.random() < cfg.flap_probability)
    return seq


def _recovery_intervals(detector, seq: Sequence[bool]) -> int | None:
    """
    Intervals from the first FAILED transition until the first HEALTHY one
    after it. None if the run never failed or never recovered.
    """
    state_name = lambda s: (s.value if hasattr(s, "value") else str(s))
    failed_at = None
    for i, missed in enumerate(seq):
        detector.update(missed)
        name = state_name(detector.state)
        if failed_at is None and name == "FAILED":
            failed_at = i
        elif failed_at is not None and name == "HEALTHY":
            return i - failed_at
    return None


def _detection_interval(detector, seq: Sequence[bool]) -> int | None:
    """Intervals from fault onset (index 0 of the sequence) to FAILED."""
    state_name = lambda s: (s.value if hasattr(s, "value") else str(s))
    for i, missed in enumerate(seq):
        detector.update(missed)
        if state_name(detector.state) == "FAILED":
            return i
    return None


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class SweepPoint:
    """One hysteresis ratio's measured outcome."""

    ratio: float
    f_thresh: int
    r_thresh: int
    n: int
    flap_count: int
    recovery_intervals: float | None
    recovery_s: float | None
    detection_intervals: float | None
    detection_s: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ratio": self.ratio,
            "f_thresh": self.f_thresh,
            "r_thresh": self.r_thresh,
            "n": self.n,
            "flap_count": self.flap_count,
            "recovery_intervals": self.recovery_intervals,
            "recovery_s": self.recovery_s,
            "detection_intervals": self.detection_intervals,
            "detection_s": self.detection_s,
        }


@dataclass
class ParetoPoint:
    """A non-dominated (recovery_time, flap_count) pair — lower is better on both."""

    ratio: float
    recovery_s: float
    flap_count: int

    def as_dict(self) -> dict[str, Any]:
        return {"ratio": self.ratio, "recovery_s": self.recovery_s, "flap_count": self.flap_count}


@dataclass
class SweepReport:
    run_id: str
    generated_at: datetime
    workload: dict[str, Any]
    points: list[SweepPoint] = field(default_factory=list)
    naive_flap_count: int = 0
    naive_recovery_s: float | None = None
    pareto_frontier: list[ParetoPoint] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=lambda: [
        "Heartbeat loss is simulated from a seeded random sequence, not captured "
        "from a real link.",
        "Recovery time is expressed in heartbeat intervals (1.0 s each by default).",
        "The naive baseline uses a single threshold and therefore has no dead band.",
    ])

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "generated_at": self.generated_at.isoformat(),
            "workload": self.workload,
            "points": [p.as_dict() for p in self.points],
            "naive": {
                "flap_count": self.naive_flap_count,
                "recovery_s": self.naive_recovery_s,
            },
            "pareto_frontier": [p.as_dict() for p in self.pareto_frontier],
            "findings": self.findings,
            "limitations": self.limitations,
        }

    def table(self) -> str:
        lines = [
            "ratio  r_thresh  flaps  detect_s  recover_s",
            "-" * 44,
        ]
        for p in self.points:
            rec = f"{p.recovery_s:.1f}" if p.recovery_s is not None else "n/a"
            det = f"{p.detection_s:.1f}" if p.detection_s is not None else "n/a"
            lines.append(f"{p.ratio:<6} {p.r_thresh:<9} {p.flap_count:<6} {det:<9} {rec}")
        lines.append(f"{'naive':<6} {'-':<9} {self.naive_flap_count:<6} {'-':<9} "
                     f"{self.naive_recovery_s if self.naive_recovery_s is not None else 'n/a'}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class HysteresisSweepHarness:
    """Sweeps hysteresis_ratio and produces the stability/recovery trade-off."""

    def __init__(self, cfg: SweepConfig):
        self.cfg = cfg

    async def run(self) -> SweepReport:
        cfg = self.cfg
        report = SweepReport(
            run_id=cfg.run_id,
            generated_at=datetime.utcnow(),
            workload=cfg.workload(),
        )

        interval_s = cfg.workload()["heartbeat_interval_s"]

        # One outcome sequence per repetition, replayed for every ratio.
        sequences = [
            build_outcome_sequence(cfg, cfg.seed + rep) for rep in range(cfg.repetitions)
        ]

        for ratio in cfg.ratios:
            r_thresh = max(1, int(round(cfg.f_thresh * ratio)))
            flaps: list[int] = []
            recoveries: list[int] = []
            detections: list[int] = []

            for seq in sequences:
                sm = HysteresisStateMachine(
                    link_id="sweep-link", f_thresh=cfg.f_thresh, n=r_thresh, r_thresh=r_thresh
                )
                det = _detection_interval(sm, seq)
                rec = _recovery_intervals(sm, seq)

                # Re-run on a fresh detector to read the flap count without the
                # first pass having already advanced the machine.
                sm2 = HysteresisStateMachine(
                    link_id="sweep-link", f_thresh=cfg.f_thresh, n=r_thresh, r_thresh=r_thresh
                )
                for missed in seq:
                    sm2.update(missed)

                flaps.append(sm2.route_flap_count)
                if rec is not None:
                    recoveries.append(rec)
                if det is not None:
                    detections.append(det)

            rec_summary = summarise(recoveries)
            det_summary = summarise(detections)
            report.points.append(SweepPoint(
                ratio=ratio,
                f_thresh=cfg.f_thresh,
                r_thresh=r_thresh,
                n=r_thresh,
                flap_count=int(summarise([float(f) for f in flaps])["median"] or 0),
                recovery_intervals=rec_summary["median"],
                recovery_s=(rec_summary["median"] * interval_s) if rec_summary["median"] is not None else None,
                detection_intervals=det_summary["median"],
                detection_s=(det_summary["median"] * interval_s) if det_summary["median"] is not None else None,
            ))
            log.info(
                "[sweep %s] ratio=%.1f r_thresh=%d flaps=%d recovery=%s",
                cfg.run_id, ratio, r_thresh,
                report.points[-1].flap_count, report.points[-1].recovery_s,
            )

        # --- naive control ---
        naive_flaps: list[int] = []
        naive_rec: list[int] = []
        for seq in sequences:
            nv = NaiveThresholdDetector(link_id="sweep-link", threshold=cfg.naive_threshold)
            rec = _recovery_intervals(nv, seq)
            nv2 = NaiveThresholdDetector(link_id="sweep-link", threshold=cfg.naive_threshold)
            for missed in seq:
                nv2.update(missed)
            naive_flaps.append(nv2.route_flap_count)
            if rec is not None:
                naive_rec.append(rec)
        report.naive_flap_count = int(summarise([float(f) for f in naive_flaps])["median"] or 0)
        naive_rec_summary = summarise(naive_rec)
        report.naive_recovery_s = (
            naive_rec_summary["median"] * interval_s
            if naive_rec_summary["median"] is not None else None
        )

        report.pareto_frontier = compute_pareto_frontier(report.points)
        report.findings = self._derive_findings(report)
        return report

    # ------------------------------------------------------------------

    @staticmethod
    def _derive_findings(report: SweepReport) -> list[str]:
        findings: list[str] = []

        # PRD §11 — >= 50% flap reduction vs naive at ratio >= 2.0.
        if report.naive_flap_count > 0:
            for p in report.points:
                if p.ratio < MIN_RATIO_FOR_TARGET:
                    continue
                reduction = (report.naive_flap_count - p.flap_count) / report.naive_flap_count
                verdict = "PASS" if reduction >= FLAP_REDUCTION_TARGET else "target not met"
                findings.append(
                    f"ratio {p.ratio}: flap count {p.flap_count} vs naive "
                    f"{report.naive_flap_count} = {reduction * 100:.0f}% reduction "
                    f"({verdict}, PRD §11 target {FLAP_REDUCTION_TARGET * 100:.0f}%)."
                )
        else:
            findings.append(
                "Flap reduction not evaluable: the naive baseline recorded 0 flaps, "
                "so raise flap_probability or lengthen the scenario."
            )

        monotone = all(
            report.points[i].flap_count <= report.points[i + 1].flap_count
            for i in range(len(report.points) - 1)
        )
        findings.append(
            "Flap count is monotonically non-increasing with ratio."
            if monotone else
            "Flap count is NOT monotonically non-increasing with ratio — the dead "
            "band is not dominating as expected; check the scenario parameters."
        )

        findings.append(
            f"Pareto frontier has {len(report.pareto_frontier)} non-dominated point(s)."
        )
        return findings


# ---------------------------------------------------------------------------
# Pareto
# ---------------------------------------------------------------------------


def compute_pareto_frontier(points: Sequence[SweepPoint]) -> list[ParetoPoint]:
    """
    Non-dominated (recovery_time, flap_count) pairs, both minimised.

    A point is dominated when another is no worse on recovery time AND no worse
    on flap count, and strictly better on at least one.
    """
    usable = [p for p in points if p.recovery_s is not None]
    frontier: list[ParetoPoint] = []
    for cand in usable:
        dominated = any(
            other is not cand
            and (other.recovery_s or 0) <= (cand.recovery_s or 0)
            and other.flap_count <= cand.flap_count
            and (
                (other.recovery_s or 0) < (cand.recovery_s or 0)
                or other.flap_count < cand.flap_count
            )
            for other in usable
        )
        if not dominated:
            frontier.append(ParetoPoint(
                ratio=cand.ratio,
                recovery_s=cand.recovery_s or 0.0,
                flap_count=cand.flap_count,
            ))
    return sorted(frontier, key=lambda p: (p.recovery_s, p.flap_count))


async def run_hysteresis_sweep(cfg: SweepConfig) -> SweepReport:
    """Convenience wrapper used by the API layer (PRD §8.1)."""
    return await HysteresisSweepHarness(cfg).run()