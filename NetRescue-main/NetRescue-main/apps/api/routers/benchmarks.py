"""
apps/api/routers/benchmarks.py — Feature D benchmarking and the two comparison
harnesses required by PRD §8.1.

Endpoints:
  POST /api/benchmarks                  run a latency/throughput benchmark
  GET  /api/benchmarks/{id}             fetch a stored benchmark result
  POST /api/benchmarks/hysteresis-sweep PRD §6.4 sweep across hysteresis ratios
  POST /api/benchmarks/scheduler-comparison  PRD §6.5 all scheduler variants

Every mutating route is operator-only (PRD §8.1 access rule).
"""

from __future__ import annotations

import logging

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from apps.api.deps import DbSession, OptionalDbSession, RequireOperator
from netrescue.benchmark.comparison import (
    ALL_RETRANSMIT_POLICIES,
    ALL_VARIANTS,
    ComparisonConfig,
    PathSpec,
    SchedulerComparisonHarness,
)
from netrescue.benchmark.runner import BenchmarkConfig, BenchmarkRunner
from netrescue.benchmark.sweep import DEFAULT_RATIOS, SweepConfig, HysteresisSweepHarness
from netrescue.core.models import Experiment

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["benchmark"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BenchmarkRequest(BaseModel):
    run_id: Optional[str] = None
    protocol: str = Field("UDP", pattern="^(UDP|TCP)$")
    target_host: str = "127.0.0.1"
    target_port: int = 9101
    message_count: int = Field(200, ge=1, le=100_000)
    message_size_bytes: int = Field(256, ge=8, le=65_536)
    send_interval_s: float = Field(0.001, ge=0.0, le=10.0)
    label: str = ""


class PathSpecModel(BaseModel):
    path_id: str
    fd_ms: float = Field(10.0, ge=0.0)
    tp_bps: float = Field(1_000_000.0, gt=0.0)
    loss_rate: float = Field(0.0, ge=0.0, le=1.0)
    cwnd_bytes: float = Field(65536.0, gt=0.0)


class SchedulerComparisonRequest(BaseModel):
    run_id: Optional[str] = None
    paths: list[PathSpecModel] = Field(..., min_length=1)
    payload_bytes: int = Field(256_000, ge=1024, le=64_000_000)
    chunk_size_bytes: int = Field(4096, ge=512, le=1_048_576)
    repetitions: int = Field(5, ge=5, le=50, description="PRD §5.4 requires at least 5")
    variants: list[str] = Field(default_factory=lambda: list(ALL_VARIANTS))
    retransmit_policies: list[str] = Field(
        default_factory=lambda: list(ALL_RETRANSMIT_POLICIES)
    )
    seed: Optional[int] = 1337


class HysteresisSweepRequest(BaseModel):
    run_id: Optional[str] = None
    f_thresh: int = Field(3, ge=1, le=50)
    ratios: list[float] = Field(default_factory=lambda: list(DEFAULT_RATIOS))
    naive_threshold: int = Field(3, ge=1, le=50)
    total_intervals: int = Field(400, ge=20, le=100_000)
    outage_start: int = Field(100, ge=0)
    outage_length: int = Field(60, ge=1)
    flap_probability: float = Field(0.12, ge=0.0, le=1.0)
    repetitions: int = Field(5, ge=5, le=50)
    seed: int = 20260101


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------


@router.post("/benchmarks")
async def run_benchmark(body: BenchmarkRequest, _: RequireOperator, db: OptionalDbSession):
    """
    Run a benchmark scenario (PRD §8.1).

    Works with fixed routes and no other feature module (PRD §6.2 acceptance).
    """
    run_id = body.run_id or f"bench-{uuid.uuid4().hex[:8]}"

    cfg = BenchmarkConfig(
        run_id=run_id,
        sender_host=body.target_host,
        sender_port=body.target_port,
        target_host=body.target_host,
        target_port=body.target_port,
        message_count=body.message_count,
        message_size_bytes=body.message_size_bytes,
        send_interval_s=body.send_interval_s,
        protocol=body.protocol,
        label=body.label or f"{body.protocol} loopback",
    )
    results = await BenchmarkRunner(cfg).run()
    payload = results.summary()

    # Persist the run so /api/runs/{id}/metrics can find it (PRD §7). The
    # measurement is real whether or not this succeeds, so a database fault is
    # reported in the response instead of failing the request.
    persisted = True
    if db is not None:
        try:
            db.add(Experiment(
                id=run_id, feature="D", status="DONE",
                config=body.model_dump(), code_version="0.1.0",
                started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                ended_at=datetime.now(timezone.utc).replace(tzinfo=None),
                environment=payload.get("environment", {}),
            ))
            db.commit()
        except Exception as exc:
            db.rollback()
            persisted = False
            log.warning("benchmark %s ran but could not be saved: %s", run_id, exc)
    else:
        persisted = False

    out = {"run_id": run_id, "persisted": persisted, **payload}
    if not persisted:
        out["persistence_error"] = (
            "PostgreSQL is not running, so this benchmark was measured but not "
            "stored. The numbers above are real; the run will not appear in "
            "history or be exportable as CSV."
        )
    return out


@router.get("/benchmarks/{run_id}")
def get_benchmark(run_id: str, db: DbSession):
    """Fetch a stored benchmark run's configuration and status."""
    exp = db.query(Experiment).filter(Experiment.id == run_id).first()
    if not exp:
        raise HTTPException(status_code=404, detail=f"Benchmark run '{run_id}' not found.")
    return {
        "run_id": exp.id,
        "feature": exp.feature,
        "status": exp.status,
        "config": exp.config,
        "environment": exp.environment,
        "started_at": exp.started_at,
        "ended_at": exp.ended_at,
    }


# ---------------------------------------------------------------------------
# Scheduler comparison (PRD §6.5)
# ---------------------------------------------------------------------------


@router.post("/benchmarks/scheduler-comparison")
async def scheduler_comparison(body: SchedulerComparisonRequest, _: RequireOperator):
    """
    Run every scheduler variant over an identical heterogeneous topology.

    Returns the full comparison table with median and p95 across repetitions,
    plus the PRD §11 pass/fail findings.
    """
    unknown = set(body.variants) - set(ALL_VARIANTS)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown scheduler variant(s): {sorted(unknown)}. Valid: {ALL_VARIANTS}",
        )

    cfg = ComparisonConfig(
        run_id=body.run_id or f"cmp-{uuid.uuid4().hex[:8]}",
        paths=[PathSpec(**p.model_dump()) for p in body.paths],
        payload_bytes=body.payload_bytes,
        chunk_size_bytes=body.chunk_size_bytes,
        repetitions=body.repetitions,
        variants=body.variants,
        retransmit_policies=body.retransmit_policies,
        seed=body.seed,
    )
    report = await SchedulerComparisonHarness(cfg).run()
    return {**report.to_dict(), "table": report.table(), "csv": report.to_csv()}


# ---------------------------------------------------------------------------
# Hysteresis sweep (PRD §6.4)
# ---------------------------------------------------------------------------


@router.post("/benchmarks/hysteresis-sweep")
async def hysteresis_sweep(body: HysteresisSweepRequest, _: RequireOperator):
    """
    Sweep hysteresis_ratio and collect (recovery_time, route_flap_count) pairs.

    Produces the trade-off curve and Pareto frontier PRD §4.4 requires.
    """
    if body.outage_start + body.outage_length > body.total_intervals:
        raise HTTPException(
            status_code=422,
            detail="outage_start + outage_length must not exceed total_intervals.",
        )

    cfg = SweepConfig(
        run_id=body.run_id or f"sweep-{uuid.uuid4().hex[:8]}",
        f_thresh=body.f_thresh,
        ratios=body.ratios,
        naive_threshold=body.naive_threshold,
        total_intervals=body.total_intervals,
        outage_start=body.outage_start,
        outage_length=body.outage_length,
        flap_probability=body.flap_probability,
        repetitions=body.repetitions,
        seed=body.seed,
    )
    report = await HysteresisSweepHarness(cfg).run()
    return {**report.to_dict(), "table": report.table()}