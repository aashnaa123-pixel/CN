"""
netrescue/transfer/scheduler.py

DB-DSP (Delay-Based Data-chunk Scheduling Policy) EWMA path scheduler
and RTX-CL (Retransmission-CL) policy — from DB-CMT (Verma et al., 2022).

- DB-DSP selects the path that minimises estimated arrival time:
    arrival_i = forward_delay_i + (chunk_size_bytes * 8 / tp_estimate_bps_i)
- RTX-CL selects the retransmission path using congestion-window size when
  delays are similar; otherwise accounts for delay and loss jointly.
- Per-path EWMA smoothing on forward-delay and throughput.

Usage::

    paths = [PathState("p1", fd_ms=10), PathState("p2", fd_ms=50)]
    sched = DBDSPScheduler(paths)
    path_id = sched.select_path(chunk_size_bytes=4096)
    # ... deliver chunk ...
    sched.record_ack(path_id, rtt_ms=22.4, size_bytes=4096)
    # On failure:
    retry_id = sched.select_rtx_path(exclude={path_id})
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger(__name__)

# EWMA smoothing factor (α in [0,1]). Higher = faster adaptation.
_ALPHA_FD = 0.25  # forward-delay smoother
_ALPHA_TP = 0.20  # throughput smoother

# Minimum throughput sentinel (1 bit/s) to avoid /0
_TP_MIN_BPS = 1.0

# Delay similarity band for RTX-CL cwnd-priority selection (ms)
_RTX_DELAY_WINDOW_MS = 5.0


class PathStatus(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


@dataclass
class PathState:
    """Per-path EWMA state for the DB-DSP scheduler."""

    path_id: str
    # Bootstrap estimates (caller should set real values if known)
    fd_estimate_ms: float = 10.0          # FD_i: forward-delay estimate
    tp_estimate_bps: float = 1_000_000.0  # TP_i: throughput estimate (1 Mbps default)
    cwnd_estimate_bytes: float = 65536.0  # cwnd_i estimate
    loss_rate: float = 0.0                # Fraction in [0, 1]
    status: PathStatus = PathStatus.UP

    # Internal: failure streak counter
    _consecutive_failures: int = field(default=0, init=False, repr=False)

    def update_from_ack(self, rtt_ms: float, size_bytes: int) -> None:
        """Update FD and TP estimates with EWMA after a successful ACK."""
        # Forward-delay is approximated as RTT/2 (probe-less)
        fd_sample = rtt_ms / 2.0
        self.fd_estimate_ms = (
            _ALPHA_FD * fd_sample + (1 - _ALPHA_FD) * self.fd_estimate_ms
        )
        # Throughput: size_bytes * 8 bits / (rtt_ms / 1000 s)
        if rtt_ms > 0:
            tp_sample = (size_bytes * 8) / (rtt_ms / 1000.0)
            self.tp_estimate_bps = (
                _ALPHA_TP * tp_sample + (1 - _ALPHA_TP) * self.tp_estimate_bps
            )
        self._consecutive_failures = 0
        log.debug(
            "[%s] ACK: fd_ms=%.1f tp_bps=%.0f",
            self.path_id, self.fd_estimate_ms, self.tp_estimate_bps,
        )

    def record_failure(self) -> None:
        """Increment failure streak; caller decides when to mark DOWN."""
        self._consecutive_failures += 1

    def arrival_time_ms(self, chunk_size_bytes: int) -> float:
        """
        Estimated arrival time for a chunk on this path (DB-DSP formula).

        arrival_i = FD_i + (chunk_bits / TP_i) * 1000
        """
        tx_time_ms = (chunk_size_bytes * 8) / max(self.tp_estimate_bps, _TP_MIN_BPS) * 1000.0
        return self.fd_estimate_ms + tx_time_ms


class BaseScheduler:
    """
    Shared surface for every chunk scheduler.

    DB-DSP and all four comparison baselines (netrescue/transfer/variants.py)
    inherit from this so that EWMA feedback, path-health bookkeeping and the
    eligibility filter behave identically across variants. Subclasses implement
    select_path() only; everything else is common.

    All variants share the same PathState objects, so a path's estimates stay
    coherent no matter which variant is driving.
    """

    #: Whether the session may move work off a path that has hit its in-flight
    #: cap. True for load-spreading schedulers; False for Single-Path TCP, whose
    #: entire meaning is that it does NOT spill to another path. A real TCP
    #: sender with a full congestion window waits — it does not migrate the
    #: connection elsewhere.
    honours_in_flight_cap: bool = True

    def __init__(self, paths: list["PathState"], max_consecutive_failures: int = 3):
        self._paths: dict[str, PathState] = {p.path_id: p for p in paths}
        self.max_consecutive_failures = max_consecutive_failures

    # ------------------------------------------------------------------
    # To be provided by subclasses
    # ------------------------------------------------------------------

    def select_path(self, chunk_size_bytes: int = 4096, exclude: set[str] | None = None) -> str | None:
        raise NotImplementedError

    def select_rtx_path(self, chunk_size_bytes: int = 4096, exclude: set[str] | None = None) -> str | None:
        """Default RTX policy: lowest estimated arrival among eligible paths."""
        candidates = self._eligible(exclude)
        if not candidates:
            return None
        return min(candidates, key=lambda p: self.arrival_ms(p, chunk_size_bytes)).path_id

    # ------------------------------------------------------------------
    # Common helpers
    # ------------------------------------------------------------------

    def arrival_ms(self, p: "PathState", chunk_size_bytes: int) -> float:
        """DB-DSP arrival estimate: FD_i + tx_time(TP_i)."""
        tx = (chunk_size_bytes * 8) / max(p.tp_estimate_bps, _TP_MIN_BPS) * 1000.0
        return p.fd_estimate_ms + tx

    def _eligible(self, exclude: set[str] | None = None) -> list["PathState"]:
        """UP paths not in the exclude set."""
        exclude = exclude or set()
        return [
            p for p in self._paths.values()
            if p.status == PathStatus.UP and p.path_id not in exclude
        ]

    # ------------------------------------------------------------------
    # Feedback — identical across variants
    # ------------------------------------------------------------------

    def record_ack(self, path_id: str, rtt_ms: float, size_bytes: int) -> None:
        """Update EWMA estimates after a successful ACK on a path."""
        if path_id in self._paths:
            self._paths[path_id].update_from_ack(rtt_ms, size_bytes)

    def record_failure(self, path_id: str) -> None:
        """
        Record a delivery failure. The path is marked DOWN once consecutive
        failures reach the threshold.
        """
        if path_id not in self._paths:
            return
        p = self._paths[path_id]
        p.record_failure()
        if p._consecutive_failures >= self.max_consecutive_failures:
            p.status = PathStatus.DOWN
            log.warning(
                "[%s] Path %s marked DOWN after %d consecutive failures.",
                type(self).__name__, path_id, p._consecutive_failures,
            )

    def mark_path_up(self, path_id: str) -> None:
        """Restore a path to UP (e.g. after Feature A reports recovery)."""
        if path_id in self._paths:
            self._paths[path_id].status = PathStatus.UP
            self._paths[path_id]._consecutive_failures = 0
            log.info("[%s] Path %s restored to UP.", type(self).__name__, path_id)

    def mark_path_down(self, path_id: str) -> None:
        """Immediately mark a path DOWN (e.g. Feature A FAILED state)."""
        if path_id in self._paths:
            self._paths[path_id].status = PathStatus.DOWN
            log.info("[%s] Path %s marked DOWN externally.", type(self).__name__, path_id)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    @property
    def paths(self) -> dict[str, "PathState"]:
        return self._paths

    @property
    def up_path_ids(self) -> list[str]:
        return [p.path_id for p in self._paths.values() if p.status == PathStatus.UP]

    def path_snapshot(self) -> list[dict]:
        """Per-path estimate snapshot (PRD §7 PathEstimate rows)."""
        return [
            {
                "path_id": p.path_id,
                "fd_estimate_ms": round(p.fd_estimate_ms, 3),
                "tp_estimate_bps": round(p.tp_estimate_bps, 1),
                "cwnd_estimate_bytes": p.cwnd_estimate_bytes,
                "loss_rate": p.loss_rate,
                "status": p.status.value,
            }
            for p in self._paths.values()
        ]


class DBDSPScheduler(BaseScheduler):
    """
    DB-DSP (Delay-Based Data-chunk Scheduling Policy) for multipath transfer.

    Selects the path minimising estimated arrival time, per DB-CMT
    (Verma et al., J. Network and Systems Management, 2022).

    RTX-CL retransmission favours the path with the larger congestion window
    when forward delays are within a similarity band; otherwise it weighs
    delay and loss together.
    """

    def __init__(self, paths: list[PathState], max_consecutive_failures: int = 3):
        super().__init__(paths, max_consecutive_failures)

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    def select_path(
        self,
        chunk_size_bytes: int = 4096,
        exclude: set[str] | None = None,
    ) -> str | None:
        """
        Select the path with the lowest estimated arrival time (DB-DSP).

        Args:
            chunk_size_bytes: Size of the chunk to schedule.
            exclude: Set of path_ids to skip (e.g. already-failed paths).

        Returns:
            path_id of the selected path, or None if no UP path is available.
        """
        candidates = self._eligible(exclude)
        if not candidates:
            log.warning("[DBDSPScheduler] No UP paths available.")
            return None

        best = min(candidates, key=lambda p: (self.arrival_ms(p, chunk_size_bytes), p.path_id))
        log.debug(
            "[DBDSPScheduler] Selected %s (arrival=%.1fms)",
            best.path_id, self.arrival_ms(best, chunk_size_bytes),
        )
        return best.path_id

    def select_rtx_path(
        self,
        chunk_size_bytes: int = 4096,
        exclude: set[str] | None = None,
    ) -> str | None:
        """
        RTX-CL: select the retransmission path.

        - If forward delays fall within _RTX_DELAY_WINDOW_MS, pick the path with
          the larger congestion window.
        - Otherwise pick the path minimising an FD + loss-adjusted cost.
        Returns None if no UP path is available.
        """
        candidates = self._eligible(exclude)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0].path_id

        # Prefer cwnd when delays are similar.
        best_fd = min(p.fd_estimate_ms for p in candidates)
        similar_delay = [
            p for p in candidates
            if abs(p.fd_estimate_ms - best_fd) <= _RTX_DELAY_WINDOW_MS
        ]
        if len(similar_delay) > 1:
            selected = max(similar_delay, key=lambda p: p.cwnd_estimate_bytes)
        else:
            def rtx_cost(p: PathState) -> float:
                loss_penalty = (
                    p.loss_rate * (chunk_size_bytes * 8)
                    / max(p.tp_estimate_bps, _TP_MIN_BPS) * 1000.0
                )
                return p.fd_estimate_ms + loss_penalty

            selected = min(candidates, key=rtx_cost)

        log.debug("[DBDSPScheduler] RTX selected %s", selected.path_id)
        return selected.path_id
