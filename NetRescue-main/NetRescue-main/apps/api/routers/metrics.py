"""apps/api/routers/metrics.py — Time-series metric query endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from apps.api.deps import DbSession
from netrescue.core.models import MetricSample

router = APIRouter(prefix="/api", tags=["metrics"])


class MetricSampleOut(BaseModel):
    id: str
    run_id: str
    timestamp: datetime
    name: str
    value: float
    unit: Optional[str]
    labels: dict

    class Config:
        from_attributes = True


@router.get("/metrics", response_model=list[MetricSampleOut])
def query_metrics(
    db: DbSession,
    run_id: Optional[str] = None,
    name: Optional[str] = None,
    since: Optional[datetime] = None,
    limit: int = 500,
):
    """
    Query time-series metric samples.

    Params:
      - run_id: filter by experiment run
      - name:   filter by metric name (e.g. 'rtt_ms')
      - since:  only return samples after this timestamp
      - limit:  max rows (default 500, max 5000)
    """
    limit = min(limit, 5000)
    q = db.query(MetricSample)
    if run_id:
        q = q.filter(MetricSample.run_id == run_id)
    if name:
        q = q.filter(MetricSample.name == name)
    if since:
        q = q.filter(MetricSample.timestamp >= since)
    return q.order_by(MetricSample.timestamp.desc()).limit(limit).all()
