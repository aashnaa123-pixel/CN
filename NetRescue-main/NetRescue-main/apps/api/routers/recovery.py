"""
apps/api/routers/recovery.py — Feature A control endpoints (PRD §8.1).

  POST /api/recovery/runs                start a recovery run (operator only)
  POST /api/recovery/runs/{id}/hysteresis  reconfigure detector params (audited)
  GET  /api/recovery/runs/{id}           fetch a run's state and metrics
  GET  /api/recovery/runs/{id}/sweep     run the hysteresis sweep for this run

Recovery is only ever reported as recovered after destination-side delivery is
confirmed (PRD §14) — the controller enforces that, not this router.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from apps.api.deps import DbSession, RequireOperator
from netrescue.benchmark.sweep import DEFAULT_RATIOS, HysteresisSweepHarness, SweepConfig
from netrescue.core.models import Experiment, HysteresisConfig, StateTransition
from netrescue.recovery.naive import NaiveThresholdDetector
from netrescue.recovery.state_machine import HysteresisStateMachine

router = APIRouter(prefix="/api", tags=["recovery"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RecoveryRunRequest(BaseModel):
    feature: str = Field("A", pattern="^A$")
    link_ids: list[str] = Field(default_factory=list)
    f_thresh: int = Field(3, ge=1, le=50)
    n: int = Field(5, ge=1, le=100)
    r_thresh: int = Field(5, ge=1, le=100)
    use_ewma: bool = False
    heartbeat_interval_s: float = Field(1.0, gt=0, le=60)


class HysteresisUpdate(BaseModel):
    link_id: str
    f_thresh: int = Field(..., ge=1, le=50)
    n: int = Field(..., ge=1, le=100)
    r_thresh: int = Field(..., ge=1, le=100)
    use_ewma: bool = False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/recovery/runs", status_code=status.HTTP_201_CREATED)
def create_recovery_run(body: RecoveryRunRequest, _: RequireOperator, db: DbSession):
    """
    Create a recovery experiment and register its hysteresis parameters.

    Warns when r_thresh <= f_thresh: the dead band is then empty and route
    flapping is likely (PRD §4.2).
    """
    run_id = f"rec-{uuid.uuid4().hex[:8]}"

    ratio = body.r_thresh / max(body.f_thresh, 1)
    warnings = []
    if body.r_thresh <= body.f_thresh:
        warnings.append(
            f"hysteresis_ratio={ratio:.2f} <= 1.0 — no dead band; route flapping "
            f"is likely. PRD §4.3 recommends a ratio of at least 1.5."
        )

    db.add(Experiment(
        id=run_id,
        feature="A",
        config=body.model_dump(),
        code_version="0.1.0",
        status="RUNNING",
        started_at=datetime.utcnow(),
    ))

    for link_id in body.link_ids:
        db.add(HysteresisConfig(
            run_id=run_id,
            link_id=link_id,
            f_thresh=body.f_thresh,
            n=body.n,
            r_thresh=body.r_thresh,
            hysteresis_ratio=ratio,
            heartbeat_interval_s=body.heartbeat_interval_s,
            use_ewma=body.use_ewma,
        ))
    db.commit()

    return {
        "run_id": run_id,
        "hysteresis_ratio": ratio,
        "links_configured": len(body.link_ids),
        "warnings": warnings,
    }


@router.post("/recovery/runs/{run_id}/hysteresis")
def update_hysteresis(run_id: str, body: HysteresisUpdate, _: RequireOperator, db: DbSession):
    """
    Update one link's detector parameters mid-run (PRD §8.1).

    Operator-only and audited — parameter tampering is a named threat in PRD §9.
    """
    exp = db.query(Experiment).filter(Experiment.id == run_id).first()
    if not exp:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    ratio = body.r_thresh / max(body.f_thresh, 1)
    cfg = db.query(HysteresisConfig).filter(
        HysteresisConfig.run_id == run_id,
        HysteresisConfig.link_id == body.link_id,
    ).first()

    if cfg is None:
        raise HTTPException(
            status_code=404,
            detail=f"Link '{body.link_id}' is not configured for run '{run_id}'.",
        )

    cfg.f_thresh = body.f_thresh
    cfg.n = body.n
    cfg.r_thresh = body.r_thresh
    cfg.hysteresis_ratio = ratio
    cfg.use_ewma = body.use_ewma
    db.commit()

    warnings = []
    if body.r_thresh <= body.f_thresh:
        warnings.append(
            f"hysteresis_ratio={ratio:.2f} <= 1.0 — no dead band; flapping likely."
        )

    return {
        "run_id": run_id,
        "link_id": body.link_id,
        "hysteresis_ratio": ratio,
        "warnings": warnings,
    }


@router.get("/recovery/runs/{run_id}")
def get_recovery_run(run_id: str, db: DbSession):
    """Fetch a recovery run with its hysteresis configuration and transitions."""
    exp = db.query(Experiment).filter(Experiment.id == run_id).first()
    if not exp:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    configs = db.query(HysteresisConfig).filter(HysteresisConfig.run_id == run_id).all()
    transitions = (
        db.query(StateTransition).filter(StateTransition.run_id == run_id).all()
    )

    # Route flap count is the number of transitions touching FAILED on each link.
    flaps: dict[str, int] = {}
    for t in transitions:
        if "FAILED" in (t.from_state, t.to_state):
            flaps[t.link_id] = flaps.get(t.link_id, 0) + 1

    return {
        "run_id": run_id,
        "feature": exp.feature,
        "status": exp.status,
        "started_at": exp.started_at,
        "ended_at": exp.ended_at,
        "hysteresis_configs": [
            {
                "link_id": c.link_id,
                "f_thresh": c.f_thresh,
                "n": c.n,
                "r_thresh": c.r_thresh,
                "hysteresis_ratio": c.hysteresis_ratio,
                "use_ewma": c.use_ewma,
            }
            for c in configs
        ],
        "transition_count": len(transitions),
        "route_flap_count_by_link": flaps,
        "route_flap_count_total": sum(flaps.values()),
    }


@router.post("/recovery/runs/{run_id}/sweep")
async def sweep_for_run(run_id: str, _: RequireOperator):
    """Run the PRD §6.4 hysteresis sweep and attach the trade-off curve."""
    report = await HysteresisSweepHarness(SweepConfig(run_id=run_id)).run()
    return {**report.to_dict(), "table": report.table()}