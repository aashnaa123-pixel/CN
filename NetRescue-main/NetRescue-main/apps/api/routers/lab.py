"""
apps/api/routers/lab.py — Recovery Lab: a live topology you can break.

  POST   /api/lab/topology          start a topology
  GET    /api/lab/topology          current state, or an honest "not running"
  DELETE /api/lab/topology          tear it down
  GET    /api/lab/routes            every node's routing table
  POST   /api/lab/failures          fail a node or a link
  POST   /api/lab/failures/clear    undo every fault
  POST   /api/lab/deliver           send a message and verify it arrived
  GET    /api/lab/history           recent deliveries and events

Every mutating route requires the WRITE permission and is audited.

Delivery is the point of the whole feature, so /api/lab/deliver reports
`delivered: false` whenever the destination does not acknowledge — including
when the destination is simply gone. There is no path through this API that
returns success for a message that was not confirmed.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from apps.api.deps import RequireOperator, RequireRead
from apps.api.runtime import lab_service
from netrescue.lab import MAX_NODES, VALID_LAYOUTS
from netrescue.security.audit import default_auditor

router = APIRouter(prefix="/api/lab", tags=["recovery-lab"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TopologyStart(BaseModel):
    nodes: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_NODES,
        description="Node ids, e.g. [\"A\", \"B\", \"C\", \"D\"]",
    )
    layout: Literal["line", "ring", "full", "mesh"] = "ring"
    heartbeat_interval_s: float = Field(1.0, gt=0.05, le=30.0)

    model_config = {
        "json_schema_extra": {
            "example": {"nodes": ["A", "B", "C", "D"], "layout": "ring"}
        }
    }


class FailureRequest(BaseModel):
    kind: Literal["node", "link"] = "node"
    target: str = Field(..., min_length=1, max_length=64)


class DeliveryRequest(BaseModel):
    source: str = Field(..., min_length=1, max_length=64)
    destination: str = Field(..., min_length=1, max_length=64)
    body: str = Field("netrescue-ping", max_length=4096)
    timeout_s: float = Field(5.0, gt=0.1, le=30.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lab():
    lab = lab_service.lab
    if lab is None or not lab.running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No topology is running. POST /api/lab/topology with a node list "
                "and a layout to start one."
            ),
        )
    return lab


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------


@router.post("/topology")
async def start_topology(body: TopologyStart, principal: RequireOperator):
    """Start (or replace) the live topology."""
    if body.layout not in VALID_LAYOUTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"layout must be one of {list(VALID_LAYOUTS)}",
        )
    await lab_service.start()
    lab = lab_service.lab
    assert lab is not None  # start() guarantees this

    try:
        status_payload = await lab.start(
            body.nodes,
            layout=body.layout,
            heartbeat_interval_s=body.heartbeat_interval_s,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    default_auditor().record(
        principal.name, "lab.topology.start",
        f"{len(body.nodes)} nodes/{body.layout}", True,
        {"nodes": body.nodes, "layout": body.layout},
    )
    return {"message": f"Topology running with {len(body.nodes)} nodes.",
            "topology": status_payload}


@router.get("/topology")
def get_topology(_: RequireRead):
    """
    Current topology state.

    When nothing is running this returns 200 with `running: false` and an
    explanation, not an error — "nothing started" is a normal state for a screen
    that renders an empty view.
    """
    lab = lab_service.lab
    if lab is None:
        return {
            "running": False,
            "nodes": [],
            "links": [],
            "failures": [],
            "message": "No topology is running. Start one to use the Recovery Lab.",
        }
    return lab.status()


@router.delete("/topology")
async def stop_topology(principal: RequireOperator):
    """Tear the topology down and release its sockets."""
    if lab_service.lab is None or not lab_service.lab.running:
        return {"message": "No topology was running."}
    await lab_service.lab.stop()
    default_auditor().record(
        principal.name, "lab.topology.stop", "all", True, {}
    )
    return {"message": "Topology stopped."}


@router.get("/routes")
def get_routes(_: RequireRead):
    """Every node's routing table, so alternate paths are visible."""
    lab = _lab()
    return {
        "routes": lab.routes(),
        "history": lab.history(50),
    }


# ---------------------------------------------------------------------------
# Faults
# ---------------------------------------------------------------------------


@router.post("/failures")
async def inject_failure(body: FailureRequest, principal: RequireOperator):
    """
    Break something on purpose.

    A node fault closes that node's real listener; a link fault marks that link
    down in every node's router. Both force a full route recalculation.
    """
    lab = _lab()
    try:
        if body.kind == "node":
            status_payload = await lab.fail_node(body.target)
        else:
            status_payload = await lab.fail_link(body.target)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No such {body.kind}: {body.target!r}. "
                   f"Known: {sorted(lab.nodes)}",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    default_auditor().record(
        principal.name, f"lab.failure.inject.{body.kind}", body.target, True,
        {"kind": body.kind, "target": body.target},
    )
    return {
        "message": f"{body.kind.capitalize()} {body.target} is down. "
                   f"Routes recalculated.",
        "topology": status_payload,
    }


@router.post("/failures/clear")
async def clear_failures(principal: RequireOperator):
    """Undo every injected fault and bring stopped nodes back."""
    lab = _lab()
    status_payload = await lab.clear_failures()
    default_auditor().record(
        principal.name, "lab.failure.clear", "all", True, {}
    )
    return {"message": "All injected faults cleared.", "topology": status_payload}


# ---------------------------------------------------------------------------
# Verified delivery
# ---------------------------------------------------------------------------


@router.post("/deliver")
async def deliver(body: DeliveryRequest, principal: RequireOperator):
    """
    Send a message and report whether it genuinely arrived.

    `delivered` is true only when the destination node acknowledged. If the
    destination has no route, is dead, or never answers, it is false and
    `reason` says why.
    """
    lab = _lab()
    try:
        result = await lab.send(
            body.source, body.destination,
            body=body.body.encode("utf-8", errors="replace"),
            timeout=body.timeout_s,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    default_auditor().record(
        principal.name, "lab.deliver",
        f"{body.source}->{body.destination}",
        bool(result["delivered"]),
        {"delivered": result["delivered"], "reason": result.get("reason")},
    )
    return result


@router.get("/history")
def get_history(_: RequireRead, limit: int = 50):
    """
    Recent deliveries and lab events.

    Events recorded before this client connected are included, so the timeline is
    not silently empty for a browser that loaded late.
    """
    lab = _lab()
    return {
        "deliveries": lab.history(min(limit, 200)),
        "events": lab_service.recent(min(limit, 200)),
    }
