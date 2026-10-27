"""
netrescue/recovery/naive.py

Naïve single-threshold failure detector — the control group for Feature A.

The PRD (§4.2, §4.4, §10.1 demo scenario 2) requires a measurable comparison
between naïve threshold detection and hysteresis-based detection. This module
provides the naïve side so the sweep harness can produce the route-flap
trade-off curve demanded by §4.4 and §6.4.

Naïve detector (the thing hysteresis improves on)
-------------------------------------------------
A two-state machine with ONE threshold on consecutive misses:

    HEALTHY --(misses >= threshold)--> FAILED
    FAILED  --(successes >= threshold)--> HEALTHY

Because the enter and exit thresholds are identical, a link whose quality
oscillates around that threshold toggles continuously. Every toggle forces a
re-route, which is precisely the "route flapping" described in §4.2.

Flap parity with the hysteresis machine
---------------------------------------
Both detectors count a flap as a change in the *usable* forwarding path:

  * naive:      every HEALTHY <-> FAILED toggle
  * hysteresis: every transition into or out of FAILED (SUSPECT does not flap)

Because both use the same definition, `flap_reduction_ratio` in
benchmark/sweep.py compares like with like. The naive detector deliberately
keeps a FAILED/HEALTHY pair of strings matching HysteresisStateMachine's
LinkState values so the two are interchangeable in reporting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from netrescue.core.events import Severity, StateTransitionEvent

log = logging.getLogger(__name__)

HEALTHY = "HEALTHY"
FAILED = "FAILED"


@dataclass
class NaiveThresholdDetector:
    """
    Single-threshold two-state detector (PRD §4.2).

    Call update(missed=True/False) once per heartbeat interval, exactly as you
    would for HysteresisStateMachine, so both can be driven by the same
    heartbeat stream in a fair comparison.
    """

    link_id: str
    threshold: int = 3
    run_id: str | None = None
    on_transition: Callable[[StateTransitionEvent], None] | None = None

    _state: str = field(default=HEALTHY, init=False)
    _consecutive_misses: int = field(default=0, init=False)
    _consecutive_successes: int = field(default=0, init=False)
    _route_flap_count: int = field(default=0, init=False)
    _transition_count: int = field(default=0, init=False)

    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_failed(self) -> bool:
        return self._state == FAILED

    @property
    def route_flap_count(self) -> int:
        return self._route_flap_count

    @property
    def transition_count(self) -> int:
        """Total state changes, including any future expansion of the state set."""
        return self._transition_count

    # ------------------------------------------------------------------

    def update(self, missed: bool) -> str:
        """Process one heartbeat interval. Returns the state after the update."""
        if missed:
            self._consecutive_misses += 1
            self._consecutive_successes = 0
        else:
            self._consecutive_successes += 1
            self._consecutive_misses = 0

        if self._state == HEALTHY:
            if self._consecutive_misses >= self.threshold:
                self._transition(FAILED)
        else:
            if self._consecutive_successes >= self.threshold:
                self._transition(HEALTHY)

        return self._state

    # ------------------------------------------------------------------

    def _transition(self, new_state: str) -> None:
        old_state = self._state
        self._state = new_state
        self._transition_count += 1

        # Every toggle in a two-state detector changes the usable path, so every
        # toggle is a flap. Same definition as the hysteresis machine.
        self._route_flap_count += 1

        log.info(
            "[naive:%s] %s → %s (misses=%d successes=%d flaps=%d)",
            self.link_id, old_state, new_state,
            self._consecutive_misses, self._consecutive_successes,
            self._route_flap_count,
        )

        if self.on_transition:
            self.on_transition(
                StateTransitionEvent(
                    link_id=self.link_id,
                    from_state=old_state,
                    to_state=new_state,
                    route_flap=True,
                    run_id=self.run_id,
                    severity=Severity.WARNING if new_state == FAILED else Severity.INFO,
                )
            )

    # ------------------------------------------------------------------

    def reset(self) -> None:
        self._state = HEALTHY
        self._consecutive_misses = 0
        self._consecutive_successes = 0
        self._route_flap_count = 0
        self._transition_count = 0


# ---------------------------------------------------------------------------
# Shared interface for the sweep harness
# ---------------------------------------------------------------------------

#: The two detectors expose the same duck-typed surface, so the sweep harness can
#: drive either without branching. Verified by tests/test_sweep_harness.py.
DetectorProtocol = object