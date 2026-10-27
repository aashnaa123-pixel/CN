"""
apps/api/routers/traffic.py — Traffic Lab: FIFO versus priority scheduling.

  POST /api/traffic/run       run one workload through one scheduler
  POST /api/traffic/compare   run the same workload through both and diff them
  GET  /api/traffic/schedulers  what this build supports

Both routes execute a real queueing computation and return the measured
latencies and throughputs. Nothing is interpolated, smoothed or invented, and
every response carries `simulated: true` with the reason, because these are
discrete-event queueing-model results and not measurements of a real network.
For real network conditions use /api/benchmarks or Linux tc/netem.

The comparison endpoint always runs both schedulers against identical arrivals,
so any difference is attributable to scheduling rather than to sampling noise.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from apps.api.deps import RequireOperator, RequireRead
from netrescue.traffic import (
    LinkConfig,
    TrafficExperiment,
    WorkloadConfig,
    run_comparison,
)
from netrescue.traffic.scheduler import SchedulerVariant
from netrescue.core.events import Severity

router = APIRouter(prefix="/api/traffic", tags=["traffic-lab"])
log = logging.getLogger(__name__)

NOT_A_MEASUREMENT = (
    "Queueing-model result, not a measurement of a real network. Use "
    "/api/benchmarks or Linux tc/netem for real network conditions."
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LinkIn(BaseModel):
    rate_bps: float = Field(5_000_000.0, gt=0, description="Link capacity in bits/s")
    propagation_ms: float = Field(1.0, ge=0, le=10_000)
    max_queue: int = Field(200, ge=1, le=10_000)


class WorkloadIn(BaseModel):
    critical_rate_hz: float = Field(40.0, ge=0, le=100_000)
    critical_size_bits: int = Field(1_600, ge=64, le=1_000_000)
    bulk_rate_hz: float = Field(200.0, ge=0, le=100_000)
    bulk_size_bits: int = Field(40_000, ge=64, le=10_000_000)
    duration_ms: float = Field(500.0, gt=0, le=600_000)
    max_queue: int = Field(200, ge=1, le=10_000)
    seed: int = Field(4242, ge=0, le=2**31 - 1)

    model_config = {
        "json_schema_extra": {
            "example": {
                "critical_rate_hz": 40, "bulk_rate_hz": 200,
                "link": {"rate_bps": 5_000_000},
            }
        }
    }


class RunRequest(BaseModel):
    scheduler: str = Field("priority", description="fifo | priority")
    workload: WorkloadIn = Field(default_factory=WorkloadIn)
    link: LinkIn = Field(default_factory=LinkIn)


class CompareRequest(BaseModel):
    workload: WorkloadIn = Field(default_factory=WorkloadIn)
    link: LinkIn = Field(default_factory=LinkIn)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/schedulers")
def list_schedulers(_: RequireRead):
    """
    What this build can run.

    The UI reads this instead of hardcoding the list, so adding a scheduler does
    not silently leave a stale option in the interface.
    """
    return {
        "schedulers": [
            {
                "id": "fifo",
                "label": "FIFO (baseline)",
                "description": (
                    "Serves strictly in arrival order and ignores traffic class. "
                    "This is the control group."
                ),
            },
            {
                "id": "priority",
                "label": "Priority",
                "description": (
                    "Serves critical traffic before bulk; bulk fills otherwise-idle "
                    "capacity. Best-effort and non-preemptive — no hard real-time "
                    "guarantee."
                ),
            },
        ],
        "traffic_classes": [
            {"id": "critical", "label": "Critical (latency-sensitive)",
             "description": "Small control and heartbeat packets with a tight latency budget."},
            {"id": "bulk", "label": "Bulk (throughput-oriented)",
             "description": "Large data packets where throughput matters more than delay."},
        ],
        "disclaimer": NOT_A_MEASUREMENT,
    }


@router.post("/run")
def run(body: RunRequest, principal: RequireOperator):
    """Run one workload through one scheduler and return what happened."""
    try:
        variant = SchedulerVariant(body.scheduler.lower())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"scheduler must be one of "
                   f"{[v.value for v in SchedulerVariant]}",
        ) from exc

    workload = WorkloadConfig(**body.workload.model_dump())
    link = LinkConfig(**body.link.model_dump())
    if link.max_queue != body.workload.max_queue:
        link.max_queue = body.workload.max_queue

    result = TrafficExperiment(workload, link, variant).run()
    log.info("[traffic] %s ran %s by %s", principal.name, variant.value, workload.to_dict())

    return {
        "run_id": uuid.uuid4().hex[:12],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "result": result.as_dict(),
        "offered_bps": workload.offered_bps(),
        "link_capacity_bps": link.rate_bps,
        "congested": workload.offered_bps() > link.rate_bps,
        "caveat": NOT_A_MEASUREMENT,
    }


@router.post("/compare")
def compare(body: CompareRequest, principal: RequireOperator):
    """
    FIFO versus priority over identical arrivals.

    Returns both results side by side plus findings that describe what the run
    actually showed — including when priority did not help, and why.
    """
    workload = WorkloadConfig(**body.workload.model_dump())
    link = LinkConfig(**body.link.model_dump())
    if link.max_queue != body.workload.max_queue:
        link.max_queue = body.workload.max_queue

    report = run_comparison(
        uuid.uuid4().hex[:12], workload, link,
        variants=(SchedulerVariant.FIFO, SchedulerVariant.PRIORITY),
    )

    payload = report.to_dict()
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload["offered_bps"] = workload.offered_bps()
    payload["link_capacity_bps"] = link.rate_bps
    payload["congested"] = workload.offered_bps() > link.rate_bps
    payload["caveat"] = NOT_A_MEASUREMENT
    payload["table"] = report.table()
    log.info("[traffic] %s compared fifo vs priority", principal.name)
    return payload
