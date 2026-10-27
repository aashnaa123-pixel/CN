"""apps/api/routers/experiments.py — Experiment lifecycle endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from apps.api.deps import DbSession
from netrescue.core.models import Experiment

router = APIRouter(prefix="/api", tags=["experiments"])


class ExperimentCreate(BaseModel):
    feature: str  # "A", "C", "D"
    config: dict[str, Any] = {}
    code_version: Optional[str] = None
    environment: dict[str, Any] = {}


class ExperimentOut(BaseModel):
    id: str
    feature: str
    config: dict[str, Any]
    code_version: Optional[str]
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    status: str
    environment: dict[str, Any]

    class Config:
        from_attributes = True


@router.get("/experiments", response_model=list[ExperimentOut])
def list_experiments(db: DbSession, feature: Optional[str] = None):
    q = db.query(Experiment)
    if feature:
        q = q.filter(Experiment.feature == feature)
    return q.order_by(Experiment.started_at.desc()).all()


@router.post("/experiments", response_model=ExperimentOut, status_code=status.HTTP_201_CREATED)
def create_experiment(body: ExperimentCreate, db: DbSession):
    """Create and immediately start an experiment run."""
    if body.feature not in ("A", "C", "D"):
        raise HTTPException(status_code=422, detail="feature must be 'A', 'C', or 'D'.")
    exp = Experiment(
        feature=body.feature,
        config=body.config,
        code_version=body.code_version,
        environment=body.environment,
        started_at=datetime.utcnow(),
        status="RUNNING",
    )
    db.add(exp)
    db.commit()
    db.refresh(exp)
    return exp


@router.get("/experiments/{exp_id}", response_model=ExperimentOut)
def get_experiment(exp_id: str, db: DbSession):
    exp = db.query(Experiment).filter(Experiment.id == exp_id).first()
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found.")
    return exp


@router.post("/experiments/{exp_id}/stop", response_model=ExperimentOut)
def stop_experiment(exp_id: str, db: DbSession):
    """Mark an experiment as DONE."""
    exp = db.query(Experiment).filter(Experiment.id == exp_id).first()
    if not exp:
        raise HTTPException(status_code=404, detail="Experiment not found.")
    if exp.status not in ("RUNNING",):
        raise HTTPException(status_code=409, detail=f"Experiment is already {exp.status}.")
    exp.status = "DONE"
    exp.ended_at = datetime.utcnow()
    db.commit()
    db.refresh(exp)
    return exp
