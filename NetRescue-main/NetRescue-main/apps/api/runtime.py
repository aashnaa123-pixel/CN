"""
apps/api/runtime.py — the process-wide live lab, wired to the event stream.

One LabRuntime per server process, created on startup and torn down on shutdown.
Its events are forwarded to the WebSocket broker so the dashboard sees node
starts, link failures, route updates and delivery confirmations as they happen
rather than only on poll.

Deliberately in-process and in-memory: the topology a user is experimenting with
is ephemeral. Anything worth keeping is persisted explicitly through the
experiment endpoints, so losing this on restart loses nothing.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from netrescue.lab import LabRuntime
from netrescue.playground import PlaygroundTopology, example_topology

log = logging.getLogger(__name__)


class LabService:
    """
    Owns the LabRuntime and fans its events out to WebSocket subscribers.

    The event sink is synchronous (it is called from deep inside packet handling
    and must never block or raise), so it hands work to the loop instead of
    touching subscriber queues directly.
    """

    def __init__(self) -> None:
        self._lab: LabRuntime | None = None
        self._broker: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._recent: list[dict] = []
        self._observers: list[Any] = []

    # ------------------------------------------------------------------

    def attach(self, broker: Any) -> None:
        """Bind to the broker once it exists (broker lives in system.py)."""
        self._broker = broker

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def lab(self) -> LabRuntime | None:
        return self._lab

    async def start(self) -> None:
        if self._lab is None:
            self._lab = LabRuntime(on_event=self._on_event)

    async def stop(self) -> None:
        if self._lab is not None:
            await self._lab.stop()
            self._lab = None

    def attach_observer(self, observer: Any) -> None:
        """
        Let a caller watch every lab event.

        The playground uses this to journal node starts, detector transitions
        and delivery confirmations as they happen, rather than only when the
        browser next asks. An observer that raises is ignored — telemetry must
        never take the forwarding path down.

        Idempotent: lifespan runs once per application, and a TestClient can
        enter and leave many times in one process. Re-attaching the same observer
        would otherwise journal every event once per past startup.
        """
        if observer not in self._observers:
            self._observers.append(observer)

    def _dispatch(self, event_type: str, payload: dict) -> None:
        for observer in list(self._observers):
            try:
                observer(event_type, payload)
            except Exception as exc:
                log.debug("lab observer raised on %s: %s", event_type, exc)

    def reset(self) -> None:
        """Drop the runtime without touching the event sink (used by tests)."""
        self._lab = None
        self._recent.clear()

    # ------------------------------------------------------------------

    def _on_event(self, event_type: str, payload: dict) -> None:
        """Called from the lab, possibly off-loop. Must not raise or block."""
        event = {"type": event_type, "payload": payload}
        try:
            self._recent.append(event)
            del self._recent[:-200]
        except Exception:
            pass

        self._dispatch(event_type, payload)

        loop, broker = self._loop, self._broker
        if loop is None or broker is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        try:
            if running is loop:
                broker.publish(event)
            else:
                loop.call_soon_threadsafe(broker.publish, event)
        except Exception as exc:
            log.debug("publishing %s failed: %s", event_type, exc)

    def recent(self, limit: int = 50) -> list[dict]:
        """
        Events emitted before a client connected.

        A browser that loads the Recovery Lab a moment after a fault would
        otherwise see an empty timeline and conclude nothing happened.
        """
        return self._recent[-limit:]


lab_service = LabService()


# ---------------------------------------------------------------------------
# Playground
# ---------------------------------------------------------------------------


class PlaygroundService:
    """
    Holds what the browser has drawn, and the journal of what actually happened.

    The draft topology lives here rather than in the browser because it has to
    outlive a page reload to mean anything, and because starting it means
    opening real sockets — which is a server decision, made from a server-side
    copy of the same graph. The browser holds a rendering; this holds the
    network.

    The journal records results as the backend produces them. It never records a
    step the caller intended, only the one that came back.
    """

    def __init__(self) -> None:
        self.topology: PlaygroundTopology = example_topology()
        self.applied: str | None = None      # signature of the running topology
        self.source: str | None = None
        self.destination: str | None = None
        self.events: list[dict] = []
        self.experiments: list[dict] = []
        self.route: list[str] | None = None
        self.previous_route: list[str] | None = None
        self.route_changes = 0
        self.last_delivery: dict | None = None
        self.packets = {"sent": 0, "delivered": 0, "failed": 0}
        self._sequence = 0

    # ------------------------------------------------------------------

    def record(self, category: str, message: str, severity: str = "info",
               detail: Any = None) -> dict:
        """Append one timestamped entry to the experiment journal."""
        self._sequence += 1
        event = {
            "seq": self._sequence,
            "ts": time.time(),
            "category": category,
            "message": message,
            "severity": severity,
            "detail": detail if detail is not None else {},
        }
        self.events.append(event)
        del self.events[:-1000]
        return event

    def clear_events(self) -> None:
        self.events = []

    # ------------------------------------------------------------------

    #: Lab events worth a line in the timeline. Anything not listed here is
    #: per-packet noise (every forwarded chunk, every heartbeat) that would bury
    #: the findings under traffic the operator did not ask about.
    JOURNALLED_EVENTS = {
        "transfer.delivery": ("delivery", "info", "Packet delivered"),
        "transfer.delivery_failed": ("delivery", "warning", "Delivery failed"),
        "recovery.failure_injected": ("failure", "warning", "Failure injected"),
        "recovery.node_restored": ("restore", "info", "Node restored"),
        "recovery.link_restored": ("restore", "info", "Link restored"),
        "state_machine.transition": ("detector", "info", "Detector transition"),
        "router.route_updated": ("route", "info", "Route updated"),
        "router.unreachable": ("route", "warning", "Destination unreachable"),
        "node.started": ("lifecycle", "info", "Node listener started"),
        "node.stopped": ("lifecycle", "warning", "Node listener stopped"),
    }

    def observe(self, event_type: str, payload: dict) -> None:
        """
        Journal a live lab event.

        Called for every event the runtime emits, from deep inside packet
        handling. It must be cheap and must not raise: a journal that broke
        forwarding would be a worse bug than a missing line.
        """
        mapping = self.JOURNALLED_EVENTS.get(event_type)
        if mapping is None:
            return
        category, severity, label = mapping
        self.record(category, f"{label}: {_describe(event_type, payload)}",
                    severity, payload)

    def reset(self, topology: PlaygroundTopology | None = None) -> None:
        self.topology = topology or example_topology()
        self.applied = None
        self.source = None
        self.destination = None
        self.route = None
        self.previous_route = None
        self.route_changes = 0
        self.packets = {"sent": 0, "delivered": 0, "failed": 0}
        self.events = []
        self.experiments = []


playground_service = PlaygroundService()


# ---------------------------------------------------------------------------
# Multipath transfer lab
# ---------------------------------------------------------------------------


class TransferLabService:
    """
    Owns one transfer workspace and the bytes it holds.

    Deliberately single-workspace. Two concurrent experiments in one browser
    would mean two sets of chunk counters and two sets of path statistics to keep
    apart, and the only thing that keeps them apart is remembering which is which
    at every call site. Uploading a second file replaces the first outright, so
    no experiment can inherit another's file, chunks or measurements — by
    construction rather than by discipline.

    Uploaded bytes live here and nowhere else. Nothing is written to disk, so
    cleanup is dropping the reference, and there is no temporary file with a
    caller-influenced name to sweep.
    """

    def __init__(self) -> None:
        self._workspace: Any = None
        self._data: bytes | None = None
        self._task: Any = None
        self.comparisons: list[dict] = []

    # ------------------------------------------------------------------

    def active(self) -> Any:
        """
        The current workspace, or None.

        Expired workspaces are dropped on read, so an abandoned upload does not
        sit in memory until someone notices.
        """
        if self._workspace is not None and self._expired(self._workspace):
            self.reset()
        return self._workspace

    @staticmethod
    def _expired(workspace) -> bool:
        from netrescue.xferlab import WORKSPACE_TTL_S

        return (time.time() - workspace.created_at) > WORKSPACE_TTL_S

    def install(self, workspace, data: bytes) -> None:
        """Adopt a new workspace, discarding whatever was held before."""
        self._cancel_task()
        self._workspace = workspace
        self._data = data

    def data_for(self, transfer_id: str) -> bytes | None:
        """The bytes for this exact transfer, or None if it is not ours."""
        if self._workspace is None or self._workspace.transfer_id != transfer_id:
            return None
        return self._data

    def attach_task(self, transfer_id: str, task) -> None:
        self._task = task

    async def cancel(self, transfer_id: str) -> bool:
        """Cancel the running task, if it is this transfer's."""
        if self._task is None or self._task.done():
            return False
        if self._workspace is None or self._workspace.transfer_id != transfer_id:
            return False
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        return True

    def _cancel_task(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    def reset(self) -> None:
        self._cancel_task()
        self._workspace = None
        self._data = None

    def next_run_id(self) -> str:
        return f"xferlab-{uuid.uuid4().hex[:10]}"

    def record_comparison(self, kind: str, payload: dict) -> None:
        entry = {"kind": kind, "ts": time.time(), **payload}
        self.comparisons.append(entry)
        del self.comparisons[:-10]
        if self._workspace is not None:
            self._workspace.record(
                f"comparison.{kind}",
                f"{kind.capitalize()} comparison completed "
                f"({payload.get('run_id', '')}).".strip(),
                "info", {"run_id": payload.get("run_id")},
            )


transfer_lab = TransferLabService()


def _describe(event_type: str, payload: dict) -> str:
    """One readable line for a lab event, built only from fields it carries."""
    path = payload.get("path")
    if isinstance(path, list) and len(path) > 1:
        hop = " → ".join(path)
    else:
        hop = None

    if event_type in ("transfer.delivery", "transfer.delivery_failed"):
        head = f"{payload.get('src') or payload.get('source')} → " \
               f"{payload.get('destination')}"
        return f"{head} via {hop}" if hop else f"{head} — {payload.get('reason')}"
    if event_type == "state_machine.transition":
        return (
            f"{payload.get('link_id')} {payload.get('from_state')} → "
            f"{payload.get('to_state')}"
        )
    if event_type == "router.route_updated":
        detail = payload.get("action") or payload.get("state") or ""
        if payload.get("link_id"):
            return f"{payload['link_id']} {detail}".strip()
        if payload.get("destination"):
            return (
                f"{payload.get('node_id')} → {payload['destination']} "
                f"next hop {payload.get('next_hop')}"
            )
        return str(payload.get("reason") or detail or "route recalculated")
    if event_type == "router.unreachable":
        return f"{payload.get('destination')}: {payload.get('reason')}"
    if event_type == "recovery.failure_injected":
        return f"{payload.get('kind')} {payload.get('target')}"
    if event_type == "recovery.node_restored":
        return f"node {payload.get('node_id')} is accepting traffic again"
    if event_type == "recovery.link_restored":
        return f"link {payload.get('link_id')} is up"
    if event_type in ("node.started", "node.stopped"):
        return f"{payload.get('node_id')} on port {payload.get('port', '—')}"
    return str(payload.get("reason") or event_type)
