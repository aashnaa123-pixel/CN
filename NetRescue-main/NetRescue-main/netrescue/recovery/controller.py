"""
netrescue/recovery/controller.py

Feature A control loop (PRD §4.3, §4.4).

Wires the three existing pieces together:

    HeartbeatReceiver  ->  HysteresisStateMachine  ->  OverlayRouter
         (liveness)            (three-state decision)      (forwarding state)

and produces the measurements the PRD asks for:

  * failure detection time   — fault injected -> FAILED transition
  * recovery time            — fault injected -> first verified delivery
  * route flap count         — per link, per run
  * delivery accounting      — exact sent / received counts

Verification rule (PRD §14): recovery is claimed only after *destination-side
delivery* is confirmed, never merely because a route was recomputed. The
controller therefore requires an explicit `confirm_delivery()` before it will
record a recovery time.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Protocol

from netrescue.core.events import EventType, Severity, StateTransitionEvent
from netrescue.recovery.router import OverlayRouter, RouteResult
from netrescue.recovery.state_machine import HysteresisStateMachine, LinkState

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Detector protocol
# ---------------------------------------------------------------------------


class Detector(Protocol):
    """Anything the controller can drive — hysteresis or naive."""

    def update(self, missed: bool): ...

    @property
    def route_flap_count(self) -> int: ...


# ---------------------------------------------------------------------------
# Per-link runtime record
# ---------------------------------------------------------------------------


@dataclass
class LinkRuntime:
    """Everything the controller tracks for one monitored link."""

    link_id: str
    detector: Detector
    fault_injected_at: float | None = None
    detected_failed_at: float | None = None
    detection_time_s: float | None = None
    recovery_time_s: float | None = None
    flap_count: int = 0

    @property
    def is_down(self) -> bool:
        state = getattr(self.detector, "state", None)
        if state is None:
            return False
        # HysteresisStateMachine uses an Enum; NaiveThresholdDetector uses str.
        return (state.value if isinstance(state, LinkState) else str(state)) == "FAILED"


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class RecoveryController:
    """
    Drives failure detection and route maintenance for a set of monitored links.

    Usage::

        controller = RecoveryController(router, source="A", destination="D")
        controller.register_link("A-B", HysteresisStateMachine(link_id="A-B"))
        controller.register_link("B-C", HysteresisStateMachine(link_id="B-C"))

        # each heartbeat interval, for each link:
        controller.heartbeat("A-B", missed=False)

        # to recover:
        route = controller.route_to("D")
        controller.confirm_delivery("A-B", message_id="msg-42")
    """

    def __init__(
        self,
        router: OverlayRouter,
        source: str | None = None,
        destination: str | None = None,
        hop_limit: int = 8,
        on_event: Callable[[str, dict], None] | None = None,
    ):
        self.router = router
        self.source = source
        self.destination = destination
        self.hop_limit = hop_limit
        self._links: dict[str, LinkRuntime] = {}
        self._on_event = on_event

        # Delivery accounting (PRD §4.4 — exact counts, not estimates).
        self.sent_count = 0
        self.received_count = 0
        self.delivered_message_ids: set[str] = set()

        self._active_route: RouteResult | None = None
        self._unreachable_since: float | None = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_link(self, link_id: str, detector: Detector) -> None:
        """Attach a detector to a link and mirror its transitions into the router."""
        rt = LinkRuntime(link_id=link_id, detector=detector)

        # Wrap the detector's own callback so we observe every transition without
        # the caller having to fan events out by hand.
        original_cb = getattr(detector, "on_transition", None)

        def _relay(evt: StateTransitionEvent) -> None:
            rt.flap_count = detector.route_flap_count
            self._on_state_change(rt, evt)
            if original_cb:
                original_cb(evt)

        try:
            detector.on_transition = _relay  # type: ignore[attr-defined]
        except AttributeError:  # frozen / read-only detector
            log.debug("[%s] detector does not accept on_transition; polling instead", link_id)

        self._links[link_id] = rt
        log.info("Registered link %s", link_id)

    def unregister_link(self, link_id: str) -> None:
        self._links.pop(link_id, None)

    @property
    def links(self) -> dict[str, LinkRuntime]:
        return dict(self._links)

    # ------------------------------------------------------------------
    # Heartbeat intake
    # ------------------------------------------------------------------

    def heartbeat(self, link_id: str, missed: bool) -> str | None:
        """
        Feed one heartbeat interval result for a link.

        Returns the resulting state value, or None if the link is unknown.
        """
        rt = self._links.get(link_id)
        if rt is None:
            log.warning("heartbeat for unregistered link %s", link_id)
            return None

        before = rt.is_down
        state = rt.detector.update(missed)
        after = rt.is_down
        rt.flap_count = rt.detector.route_flap_count

        # Record detection time on the HEALTHY/SUSPECT -> FAILED edge.
        if after and not before and rt.detected_failed_at is None:
            rt.detected_failed_at = time.monotonic()
            if rt.fault_injected_at is not None:
                rt.detection_time_s = rt.detected_failed_at - rt.fault_injected_at
                self._emit(
                    "state_machine.transition",
                    {
                        "link_id": link_id,
                        "detection_time_s": round(rt.detection_time_s, 4),
                        "run_id": None,
                    },
                    Severity.WARNING,
                )

        value = state.value if isinstance(state, LinkState) else str(state)
        return value

    # ------------------------------------------------------------------
    # Fault injection (PRD §6.4 primitives, controller-side bookkeeping)
    # ------------------------------------------------------------------

    def inject_fault(self, link_id: str) -> float:
        """
        Mark a link as faulty *now*.

        This only records the timestamp; the link is marked FAILED by the
        detector once enough heartbeats are actually missed, which is what
        makes detection-time measurement meaningful.
        """
        rt = self._links.get(link_id)
        if rt is None:
            raise KeyError(f"unknown link {link_id!r}")
        rt.fault_injected_at = time.monotonic()
        rt.detected_failed_at = None
        rt.detection_time_s = None
        rt.recovery_time_s = None
        self._emit(
            "emulation.fault_injected",
            {"link_id": link_id, "at": rt.fault_injected_at},
            Severity.WARNING,
        )
        return rt.fault_injected_at

    def clear_fault(self, link_id: str) -> None:
        rt = self._links.get(link_id)
        if rt is not None:
            rt.fault_injected_at = None

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _on_state_change(self, rt: LinkRuntime, evt: StateTransitionEvent) -> None:
        """Mirror a detector transition into the router."""
        if evt.to_state == "FAILED":
            if rt.link_id in self.router._link_ids.values():
                self.router.mark_failed(rt.link_id)
            else:
                # Link not present in the topology graph; fall back to node form.
                self.router.mark_failed(rt.link_id)
            self._active_route = None
            self._emit("router.unreachable", {"link_id": rt.link_id, "reason": "link_failed"}, Severity.WARNING)
        elif evt.to_state == "HEALTHY" and evt.from_state == "FAILED":
            self.router.mark_healthy(rt.link_id)
            self._active_route = None
            self._emit(
                "router.route_updated",
                {"link_id": rt.link_id, "route_version": self.router.current_version},
                Severity.INFO,
            )

    def route_to(self, destination: str | None = None) -> RouteResult:
        """
        Compute (and cache) the active route.

        A cached route is invalidated whenever any link changes state, so the
        caller always receives a route consistent with current link health.
        """
        dest = destination or self.destination
        src = self.source
        if not src or not dest:
            raise ValueError("controller needs both source and destination to route")

        if self._active_route is None:
            result = self.router.best_route(src, dest, hop_limit=self.hop_limit)
            if result.reachable:
                self._active_route = result
                self._unreachable_since = None
            else:
                if self._unreachable_since is None:
                    self._unreachable_since = time.monotonic()
                self._emit(
                    "router.unreachable",
                    {"source": src, "destination": dest, "reason": result.reason},
                    Severity.WARNING,
                )
            return result

        return self._active_route

    @property
    def is_degraded(self) -> bool:
        """True when no route exists — visible degraded mode (PRD §2.2 rule 3)."""
        return self._active_route is None or not self._active_route.reachable

    # ------------------------------------------------------------------
    # Delivery accounting
    # ------------------------------------------------------------------

    def send(self, count: int = 1) -> int:
        self.sent_count += count
        return self.sent_count

    def confirm_delivery(self, link_id: str, message_id: str | None = None) -> float | None:
        """
        Record a destination-verified delivery and, if this link had a fault,
        close out its recovery time (PRD §14 — destination-side verification).

        Returns the recovery time in seconds, or None if the link had no
        recorded fault.
        """
        rt = self._links.get(link_id)
        self.received_count += 1
        if message_id is not None:
            if message_id in self.delivered_message_ids:
                log.warning("[%s] duplicate delivery id %s ignored", link_id, message_id)
                return rt.recovery_time_s if rt else None
            self.delivered_message_ids.add(message_id)

        if rt is not None and rt.fault_injected_at is not None and rt.recovery_time_s is None:
            rt.recovery_time_s = time.monotonic() - rt.fault_injected_at
            log.info(
                "[%s] recovery verified after %.4fs (detection=%.4fs)",
                link_id, rt.recovery_time_s,
                rt.detection_time_s if rt.detection_time_s is not None else float("nan"),
            )
            self._emit(
                "transfer.complete",
                {
                    "link_id": link_id,
                    "recovery_time_s": round(rt.recovery_time_s, 4),
                    "detection_time_s": rt.detection_time_s,
                    "message_id": message_id,
                },
                Severity.INFO,
            )
        return rt.recovery_time_s if rt else None

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Per-link run summary, suitable for MetricSample rows (PRD §7)."""
        return {
            "sent_count": self.sent_count,
            "received_count": self.received_count,
            "loss_count": max(0, self.sent_count - self.received_count),
            "total_flaps": sum(rt.flap_count for rt in self._links.values()),
            "links": {
                link_id: {
                    "flap_count": rt.flap_count,
                    "detection_time_s": rt.detection_time_s,
                    "recovery_time_s": rt.recovery_time_s,
                    "state": getattr(getattr(rt.detector, "state", None), "value", None),
                }
                for link_id, rt in self._links.items()
            },
        }

    def reset(self) -> None:
        self.sent_count = 0
        self.received_count = 0
        self.delivered_message_ids.clear()
        self._active_route = None
        self._unreachable_since = None
        for rt in self._links.values():
            rt.fault_injected_at = None
            rt.detected_failed_at = None
            rt.detection_time_s = None
            rt.recovery_time_s = None
            rt.flap_count = 0
            if hasattr(rt.detector, "reset"):
                rt.detector.reset()

    # ------------------------------------------------------------------

    def _emit(self, event_type: str, payload: dict, severity: Severity) -> None:
        if self._on_event:
            self._on_event(event_type, {**payload, "severity": severity.value, "ts": datetime.utcnow().isoformat()})