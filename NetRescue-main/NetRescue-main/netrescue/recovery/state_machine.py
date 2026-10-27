"""
netrescue/recovery/state_machine.py

Hysteresis three-state failure detector (PRD §4.3).

States:  HEALTHY → SUSPECT → FAILED → HEALTHY
         No direct HEALTHY ↔ FAILED transitions.

Key parameters:
  f_thresh   — consecutive missed heartbeats before entering SUSPECT  (default 3)
  n          — consecutive missed heartbeats before entering FAILED    (default 5)
  r_thresh   — consecutive successful heartbeats to return to HEALTHY  (default 5)
  hysteresis_ratio = r_thresh / f_thresh

Optional EWMA filter on the miss count for smoother convergence.

Usage::

    def on_transition(evt: StateTransitionEvent): ...

    sm = HysteresisStateMachine(
        link_id="A-B", f_thresh=3, n=5, r_thresh=5,
        on_transition=on_transition
    )
    sm.update(missed=True)   # call once per heartbeat interval
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from netrescue.core.events import StateTransitionEvent, Severity

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


class LinkState(str, Enum):
    HEALTHY = "HEALTHY"
    SUSPECT = "SUSPECT"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


TransitionCallback = Callable[[StateTransitionEvent], None]


@dataclass
class HysteresisStateMachine:
    """
    Three-state hysteresis failure detector per link (PRD §4.3).

    call update(missed=True/False) after every heartbeat interval.
    """

    link_id: str
    f_thresh: int = 3            # HEALTHY → SUSPECT
    n: int = 5                   # SUSPECT → FAILED
    r_thresh: int = 5            # FAILED → HEALTHY
    use_ewma: bool = False       # Optional EWMA filter on miss quality
    ewma_alpha: float = 0.3      # EWMA smoothing factor (0 < α ≤ 1)
    run_id: str | None = None
    on_transition: TransitionCallback | None = None

    # Internal state
    _state: LinkState = field(default=LinkState.HEALTHY, init=False)
    _consecutive_misses: int = field(default=0, init=False)
    _consecutive_successes: int = field(default=0, init=False)
    _route_flap_count: int = field(default=0, init=False)
    _ewma_quality: float = field(default=1.0, init=False)  # 1.0 = perfect, 0.0 = all miss

    def __post_init__(self) -> None:
        if self.r_thresh <= self.f_thresh:
            log.warning(
                "[%s] hysteresis_ratio=%.2f < 1 (r_thresh=%d, f_thresh=%d). "
                "This means no hysteresis dead band — route flapping likely.",
                self.link_id, self.r_thresh / max(self.f_thresh, 1), self.r_thresh, self.f_thresh,
            )

    @property
    def state(self) -> LinkState:
        return self._state

    @property
    def hysteresis_ratio(self) -> float:
        return self.r_thresh / max(self.f_thresh, 1)

    @property
    def route_flap_count(self) -> int:
        return self._route_flap_count

    def update(self, missed: bool) -> LinkState:
        """
        Process one heartbeat interval result.

        Args:
            missed: True if heartbeat was NOT received in this interval.

        Returns:
            Current state after update.
        """
        # --- EWMA filter (optional) ---
        if self.use_ewma:
            sample = 0.0 if missed else 1.0
            self._ewma_quality = (
                self.ewma_alpha * sample + (1 - self.ewma_alpha) * self._ewma_quality
            )
            # Convert smoothed quality to effective miss/success
            effective_miss = self._ewma_quality < 0.5
        else:
            effective_miss = missed

        # --- Update counters ---
        if effective_miss:
            self._consecutive_misses += 1
            self._consecutive_successes = 0
        else:
            self._consecutive_successes += 1
            self._consecutive_misses = 0

        # --- State transitions ---
        old_state = self._state

        if self._state == LinkState.HEALTHY:
            if self._consecutive_misses >= self.f_thresh:
                self._transition(LinkState.SUSPECT)

        elif self._state == LinkState.SUSPECT:
            if self._consecutive_misses >= self.n:
                self._transition(LinkState.FAILED)
            elif self._consecutive_successes >= self.r_thresh:
                # Recover from SUSPECT without going through FAILED
                self._transition(LinkState.HEALTHY)

        elif self._state == LinkState.FAILED:
            if self._consecutive_successes >= self.r_thresh:
                self._transition(LinkState.HEALTHY)

        return self._state

    def _transition(self, new_state: LinkState) -> None:
        old_state = self._state
        self._state = new_state

        # Route flap accounting (PRD §4.3, §11).
        #
        # A "route flap" is a change in the *usable* forwarding path. Entering or
        # leaving FAILED changes it; entering SUSPECT does not, because the link is
        # still forwarding. Counting both directions keeps the metric symmetric and
        # directly comparable with the naive two-state detector in naive.py, which
        # flaps on every HEALTHY<->FAILED toggle.
        #
        # Note: HEALTHY -> FAILED is unreachable by design (no direct transitions,
        # PRD §4.3) and is deliberately not special-cased here.
        route_flap = LinkState.FAILED in (old_state, new_state)

        if route_flap:
            self._route_flap_count += 1

        log.info(
            "[%s] %s → %s (misses=%d successes=%d flaps=%d)",
            self.link_id, old_state.value, new_state.value,
            self._consecutive_misses, self._consecutive_successes, self._route_flap_count,
        )

        if self.on_transition:
            evt = StateTransitionEvent(
                link_id=self.link_id,
                from_state=old_state.value,
                to_state=new_state.value,
                route_flap=route_flap,
                run_id=self.run_id,
                severity=Severity.WARNING if new_state == LinkState.FAILED else Severity.INFO,
            )
            self.on_transition(evt)

    def reset(self) -> None:
        """Reset state to HEALTHY — use when starting a new run."""
        self._state = LinkState.HEALTHY
        self._consecutive_misses = 0
        self._consecutive_successes = 0
        self._route_flap_count = 0
        self._ewma_quality = 1.0
