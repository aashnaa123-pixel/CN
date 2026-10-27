"""
netrescue.recovery — Feature A: hysteresis-based self-healing recovery (PRD §4).

  naive       : single-threshold detector, the control group for comparison
  state_machine : three-state hysteresis detector with a dead band
  heartbeat   : UDP heartbeat transport with HMAC auth and replay protection
  router      : topology, route calculation, versioning, loop protection
  controller  : the control loop tying all four together, plus measurements
"""

from netrescue.recovery.controller import RecoveryController
from netrescue.recovery.naive import NaiveThresholdDetector
from netrescue.recovery.router import (
    OverlayRouter,
    RouteResult,
    TopologyLink,
    TopologyNode,
)
from netrescue.recovery.state_machine import HysteresisStateMachine, LinkState

__all__ = [
    "RecoveryController",
    "NaiveThresholdDetector",
    "HysteresisStateMachine",
    "LinkState",
    "OverlayRouter",
    "RouteResult",
    "TopologyNode",
    "TopologyLink",
]