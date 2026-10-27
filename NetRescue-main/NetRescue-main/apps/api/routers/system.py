"""
apps/api/routers/system.py — topology view, run results, audit and the live
event stream (PRD §8.1).

  GET  /api/topology          nodes + links + health, for the overview screen
  GET  /api/runs/{id}/metrics raw MetricSample rows for a run
  GET  /api/runs/{id}/export.csv  CSV export; no arbitrary file access
  GET  /api/audit             audit trail, role-filtered
  GET  /api/security/status   TLS + emulation capability report
  WS   /ws/events             live event stream

`export.csv` only ever serialises rows from the database for one run id — the
path is never taken from the caller, which is the "no arbitrary file access"
control in PRD §8.1.
"""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from apps.api.deps import DbSession, RequireRead
from netrescue.core.models import AuditEvent, Experiment, Link, MetricSample, Node
from netrescue.security.tls import tls_status
from netrescue.emulation.harness import environment_report

router = APIRouter(tags=["system"])


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------


@router.get("/api/topology")
def get_topology(db: DbSession):
    """
    Full topology snapshot for the dashboard overview (PRD §8.2).

    Read-only role may call this.
    """
    nodes = db.query(Node).all()
    links = db.query(Link).all()
    active = db.query(Experiment).filter(Experiment.status == "RUNNING").all()

    return {
        "nodes": [
            {
                "node_id": n.node_id,
                "status": n.status,
                "last_seen_at": n.last_seen_at,
                "software_version": n.software_version,
            }
            for n in nodes
        ],
        "links": [
            {
                "link_id": link.link_id,
                "node_a_id": link.node_a_id,
                "node_b_id": link.node_b_id,
                "status": link.status,
                "latency_ms": link.latency_ms,
                "loss_rate": link.loss_rate,
                "capacity_bps": link.capacity_bps,
            }
            for link in links
        ],
        "active_runs": [
            {"id": e.id, "feature": e.feature, "started_at": e.started_at}
            for e in active
        ],
        "degraded": any(n.status == "DEGRADED" for n in nodes),
    }


# ---------------------------------------------------------------------------
# Run results
# ---------------------------------------------------------------------------


@router.get("/api/runs/{run_id}/metrics")
def get_run_metrics(run_id: str, db: DbSession, _: RequireRead):
    """Raw metric samples recorded for a run."""
    exp = db.query(Experiment).filter(Experiment.id == run_id).first()
    if not exp:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    samples = db.query(MetricSample).filter(MetricSample.run_id == run_id).all()
    return {
        "run_id": run_id,
        "count": len(samples),
        "samples": [
            {
                "name": s.name,
                "value": s.value,
                "unit": s.unit,
                "timestamp": s.timestamp,
                "labels": s.labels,
            }
            for s in samples
        ],
    }


@router.get("/api/runs/{run_id}/export.csv")
def export_run_csv(run_id: str, db: DbSession, _: RequireRead):
    """
    CSV export of one run's metrics.

    The run id is matched against the database only; no filesystem path is ever
    derived from caller input (PRD §8.1 "no arbitrary file access").
    """
    exp = db.query(Experiment).filter(Experiment.id == run_id).first()
    if not exp:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    samples = db.query(MetricSample).filter(MetricSample.run_id == run_id).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["run_id", "timestamp", "name", "value", "unit", "labels"])
    for s in samples:
        writer.writerow([
            run_id,
            s.timestamp.isoformat() if s.timestamp else "",
            s.name,
            s.value,
            s.unit or "",
            ";".join(f"{k}={v}" for k, v in sorted((s.labels or {}).items())),
        ])

    return {
        "run_id": run_id,
        "filename": f"run-{run_id}-metrics.csv",
        "content": buf.getvalue(),
        "rows": len(samples),
    }


# ---------------------------------------------------------------------------
# Audit + security
# ---------------------------------------------------------------------------


@router.get("/api/audit")
def get_audit(db: DbSession, _: RequireRead, limit: int = 200, result: str | None = None):
    """
    Audit trail (PRD §8.2 security/audit screen).

    Detail fields are already scrubbed at write time, so nothing secret can
    appear here even if a caller previously passed one in.

    `persistence` reports whether writes to PostgreSQL are actually landing. An
    audit trail that silently stopped recording would otherwise look identical to
    a quiet period.
    """
    from netrescue.security.audit import audit_persistence_status

    q = db.query(AuditEvent).order_by(AuditEvent.timestamp.desc())
    if result in ("ALLOW", "DENY"):
        q = q.filter(AuditEvent.result == result)
    rows = q.limit(min(limit, 1000)).all()

    return {
        "count": len(rows),
        "persistence": audit_persistence_status(),
        "entries": [
            {
                "timestamp": e.timestamp,
                "principal": e.principal,
                "action": e.action,
                "resource": e.resource,
                "result": e.result,
                "detail": e.detail,
            }
            for e in rows
        ],
    }


@router.get("/api/security/status")
def security_status(_: RequireRead):
    """TLS material and host emulation capability, for the audit screen."""
    return {"tls": tls_status(), "emulation": environment_report()}


# ---------------------------------------------------------------------------
# Live events
# ---------------------------------------------------------------------------


class EventBroker:
    """
    In-process fan-out for the WebSocket event stream.

    Deliberately in-memory: no broker dependency, nothing external, nothing
    that would violate PRD §1.2's "no cloud services, no external APIs".
    Subscribers only ever see sanitised event dicts.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        """Fan out one event. Slow consumers drop rather than block the publisher."""
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                self._subscribers.discard(q)


broker = EventBroker()


@router.websocket("/ws/events")
async def websocket_events(ws: WebSocket):
    """
    Live event stream (PRD §8.1).

    Sends a hello frame, then forwards published events. Role filtering happens
    on publish; this endpoint only relays what it is given.
    """
    await ws.accept()
    queue = broker.subscribe()
    try:
        await ws.send_json({
            "type": "hello",
            "payload": {"message": "subscribed to NetRescue events"},
        })
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Keep-alive so proxies do not drop an idle stream.
                await ws.send_json({"type": "ping"})
                continue
            await ws.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        broker.unsubscribe(queue)