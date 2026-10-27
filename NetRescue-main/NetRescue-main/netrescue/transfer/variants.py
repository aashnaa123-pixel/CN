"""
netrescue/transfer/variants.py

Baseline chunk schedulers for Feature C (PRD §5.3).

DB-DSP lives in scheduler.py because it is the primary algorithm under test.
This module supplies the four comparison baselines the PRD requires, all
sharing the same PathState objects so EWMA feedback has a single source of
truth no matter which variant is driving.

PRD §5.3 scheduler variant table
--------------------------------
===================  ==================================================
Variant              Rule
===================  ==================================================
DB-DSP               Send on path minimising estimated arrival time
Round-Robin          Cycle chunks across paths in order
Weighted RR          Allocate chunks proportional to estimated bandwidth
Single-Path TCP      Use only the lowest-RTT path
Naïve Delay-Aware    Use only forward delay, ignore throughput
===================  ==================================================

Why Naïve Delay-Aware is not Single-Path
----------------------------------------
Both rank by forward delay, but they are different policies and the PRD lists
them as separate rows:

  * Single-Path TCP is a *lower bound* — it pins all traffic to one path and
    never uses the others, so multipath buys it nothing.
  * Naïve Delay-Aware still stripes across every path; it merely ranks them by
    forward delay while ignoring bandwidth.

Ablation used here: Naïve Delay-Aware scores a path as
``FD_i + tx_time(chunk) / nominal_rate`` — identical in form to DB-DSP except
every path is assumed to share one nominal rate. Throughput differences are
therefore invisible to it, which is exactly the property being ablated.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field

from netrescue.transfer.scheduler import BaseScheduler, PathState, PathStatus

log = logging.getLogger(__name__)

#: Nominal bandwidth assumed for every path by the naïve delay-aware ablation.
#: Any positive constant works — its only job is to be *the same* for all paths.
NOMINAL_RATE_BPS = 1_000_000.0

_TP_MIN_BPS = 1.0


# ---------------------------------------------------------------------------
# Round-Robin
# ---------------------------------------------------------------------------


class RoundRobinScheduler(BaseScheduler):
    """
    Naïve baseline: cycle chunks across UP paths strictly in order.

    Ignores delay, throughput and loss entirely. This is the control group that
    DB-DSP must beat on out-of-order count (PRD §11).
    """

    def __init__(self, paths: list[PathState], max_consecutive_failures: int = 3):
        super().__init__(paths, max_consecutive_failures)
        self._order = list(self._paths.keys())
        self._cursor = 0

    def select_path(self, chunk_size_bytes: int = 4096, exclude: set[str] | None = None) -> str | None:
        exclude = exclude or set()
        eligible = set(self.up_path_ids) - exclude
        if not eligible:
            return None
        for _ in range(len(self._order)):
            pid = self._order[self._cursor % len(self._order)]
            self._cursor += 1
            if pid in eligible:
                return pid
        return sorted(eligible)[0]


# ---------------------------------------------------------------------------
# Weighted Round-Robin
# ---------------------------------------------------------------------------


@dataclass
class _WrrState:
    current_weight: int = 0


class WeightedRoundRobinScheduler(BaseScheduler):
    """
    Bandwidth-aware baseline: allocate chunks proportional to TP estimate.

    Uses smooth weighted round-robin (SWRR). The naive implementation of WRR —
    "always pick the path with the highest bandwidth" — is not round-robin at
    all: it pins every chunk to one path and defeats the purpose of striping.
    SWRR alternates smoothly, so over N selections each path receives
    ``round(N * w_i / sum(w))`` chunks.
    """

    def __init__(self, paths: list[PathState], max_consecutive_failures: int = 3):
        super().__init__(paths, max_consecutive_failures)
        # Weights track the live TP estimate; recomputed lazily from PathState.
        self._state: dict[str, _WrrState] = {pid: _WrrState() for pid in self._paths}

    def _weights(self, candidates: list[PathState]) -> dict[str, int]:
        """Integer weights proportional to current throughput estimate."""
        if not candidates:
            return {}
        tps = {p.path_id: max(p.tp_estimate_bps, _TP_MIN_BPS) for p in candidates}
        total = sum(tps.values())
        if total <= 0:
            return {p.path_id: 1 for p in candidates}
        # Scale so the largest weight is ~1000, keeping rounding error negligible.
        scale = 1000.0 / max(tps.values())
        return {pid: max(1, int(round(tp * scale))) for pid, tp in tps.items()}

    def select_path(self, chunk_size_bytes: int = 4096, exclude: set[str] | None = None) -> str | None:
        candidates = self._eligible(exclude)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0].path_id

        weights = self._weights(candidates)
        total_weight = sum(weights.values())

        best_pid, best_weight = None, None
        for p in candidates:
            st = self._state.setdefault(p.path_id, _WrrState())
            st.current_weight += weights[p.path_id]
            if best_weight is None or st.current_weight > best_weight:
                best_pid, best_weight = p.path_id, st.current_weight

        # The winner pays back its weight; classic SWRR.
        self._state[best_pid].current_weight -= total_weight
        return best_pid


# ---------------------------------------------------------------------------
# Single-Path TCP
# ---------------------------------------------------------------------------


class SinglePathScheduler(BaseScheduler):
    """
    Lower bound: pin everything to the single lowest-RTT path.

    The choice is latched on first use and held for the run. Real single-path
    TCP selects a path once and keeps it; it does not migrate because its own
    congestion inflated the RTT estimate. Re-evaluating every chunk would let
    this variant spread across paths under load, which destroys the lower bound
    it exists to provide.

    Retransmission may still spill onto a second path, which is what a real TCP
    fallback does.
    """

    # Pinning is the point of this variant, so the session must not move work
    # off the chosen path just because its in-flight window is full.
    honours_in_flight_cap = False

    def __init__(self, paths: list[PathState], max_consecutive_failures: int = 3):
        super().__init__(paths, max_consecutive_failures)
        self._pinned: str | None = None

    def select_path(self, chunk_size_bytes: int = 4096, exclude: set[str] | None = None) -> str | None:
        exclude = exclude or set()

        if self._pinned is not None and self._pinned not in exclude:
            p = self._paths.get(self._pinned)
            if p is not None and p.status == PathStatus.UP:
                return self._pinned

        # First use, or the pinned path went down: choose the lowest-delay one.
        candidates = self._eligible(exclude)
        if not candidates:
            return None
        self._pinned = min(candidates, key=lambda p: (p.fd_estimate_ms, p.path_id)).path_id
        return self._pinned

    def mark_path_down(self, path_id: str) -> None:
        super().mark_path_down(path_id)
        if path_id == self._pinned:
            self._pinned = None  # force re-selection on the next chunk

    def mark_path_up(self, path_id: str) -> None:
        super().mark_path_up(path_id)
        if self._pinned is None:
            self._pinned = None


# ---------------------------------------------------------------------------
# Naïve Delay-Aware (DB-DSP ablation)
# ---------------------------------------------------------------------------


class NaiveDelayScheduler(BaseScheduler):
    """
    DB-DSP ablation: rank by forward delay, ignore per-path throughput.

    Scores ``FD_i + tx_time / NOMINAL_RATE_BPS`` — DB-DSP's arrival-time form
    with TP_i replaced by one global nominal rate. Because the rate is the same
    for every path, only the delay term differentiates them, so the scheduler
    cannot exploit a fast-but-higher-delay path.
    """

    def __init__(self, paths: list[PathState], max_consecutive_failures: int = 3,
                 nominal_rate_bps: float = NOMINAL_RATE_BPS):
        super().__init__(paths, max_consecutive_failures)
        self.nominal_rate_bps = nominal_rate_bps

    def arrival_ms(self, p: PathState, chunk_size_bytes: int) -> float:
        """
        DB-DSP's form with TP_i replaced by one global nominal rate, so only the
        forward-delay term differentiates paths.
        """
        tx = (chunk_size_bytes * 8) / max(self.nominal_rate_bps, _TP_MIN_BPS) * 1000.0
        return p.fd_estimate_ms + tx

    def select_path(self, chunk_size_bytes: int = 4096, exclude: set[str] | None = None) -> str | None:
        candidates = self._eligible(exclude)
        if not candidates:
            return None
        return min(candidates, key=lambda p: (self.arrival_ms(p, chunk_size_bytes), p.path_id)).path_id


# ---------------------------------------------------------------------------
# Retransmission policies (PRD §5.3, §5.4)
# ---------------------------------------------------------------------------


class RtxCwndPolicy:
    """
    RTX-CWND baseline (PRD §5.4 comparison control).

    Selects the retransmission path purely by congestion-window size, ignoring
    delay and loss. This is the scheme DB-CMT's RTX-CL is measured against.
    """

    name = "RTX-CWND"

    def __init__(self, scheduler: BaseScheduler):
        self._scheduler = scheduler

    def select(self, chunk_size_bytes: int = 4096, exclude: set[str] | None = None) -> str | None:
        candidates = self._scheduler._eligible(exclude)
        if not candidates:
            return None
        return max(candidates, key=lambda p: (p.cwnd_estimate_bytes, -p.fd_estimate_ms)).path_id


class RtxClPolicy:
    """
    RTX-CL (Verma et al., 2022) — the policy under test.

    When forward delays are within a similarity window, prefer the larger
    congestion window. Otherwise weigh delay and loss together, because
    choosing purely on cwnd can strand a retransmit on a fast but lossy path.
    """

    name = "RTX-CL"

    #: Paths whose forward delays differ by less than this are treated as
    #: delay-equivalent, so cwnd becomes the deciding factor.
    DELAY_WINDOW_MS = 5.0

    def __init__(self, scheduler: BaseScheduler):
        self._scheduler = scheduler

    def select(self, chunk_size_bytes: int = 4096, exclude: set[str] | None = None) -> str | None:
        candidates = self._scheduler._eligible(exclude)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0].path_id

        best_fd = min(p.fd_estimate_ms for p in candidates)
        similar = [p for p in candidates if abs(p.fd_estimate_ms - best_fd) <= self.DELAY_WINDOW_MS]
        if len(similar) > 1:
            return max(similar, key=lambda p: p.cwnd_estimate_bytes).path_id

        def cost(p: PathState) -> float:
            tx = (chunk_size_bytes * 8) / max(p.tp_estimate_bps, _TP_MIN_BPS) * 1000.0
            # Expected number of attempts, each paying the transmission cost.
            attempts = 1.0 / max(1.0 - p.loss_rate, 1e-3)
            return p.fd_estimate_ms + tx * attempts

        return min(candidates, key=cost).path_id