"""
apps/api/routers/emulation.py — Feature D tc/netem control (PRD §8.1).

  POST /api/emulation/config    apply per-path tc/netem settings (operator only)
  GET  /api/emulation/status    host capability report
  POST /api/emulation/teardown remove the qdisc from an interface
  GET  /api/emulation/interfaces  list interfaces available for emulation

Every apply is verified and hashed before it is accepted (PRD §6.3), and each
call is audited (PRD §9 — tc/netem injection is a named threat).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from apps.api.deps import DbSession, RequireEmulation
from netrescue.core.models import TcConfig
from netrescue.emulation.harness import (
    EmulationHarness,
    environment_report,
    interface_names,
)
from netrescue.emulation.platform import EmulationUnavailable, emulation_support
from netrescue.emulation.tc_netem import TcNetemConfig, remove_config

router = APIRouter(prefix="/api", tags=["emulation"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class EmulationConfigRequest(BaseModel):
    """All nine tc/netem parameters from PRD §6.3."""

    run_id: str
    link_id: str = ""
    interface: str
    delay_ms: Optional[float] = Field(None, ge=0)
    jitter_ms: Optional[float] = Field(None, ge=0)
    delay_correlation: Optional[float] = Field(None, ge=0, le=100)
    loss_rate: Optional[float] = Field(None, ge=0, le=1)
    loss_correlation: Optional[float] = Field(None, ge=0, le=100)
    duplication_rate: Optional[float] = Field(None, ge=0, le=1)
    corruption_rate: Optional[float] = Field(None, ge=0, le=1)
    reorder_rate: Optional[float] = Field(None, ge=0, le=1)
    reorder_gap: Optional[int] = Field(None, ge=1)
    rate_limit_bps: Optional[float] = Field(None, gt=0)
    queue_depth: Optional[int] = Field(None, ge=1)
    seed: Optional[int] = None
    verify: bool = True
    strict: bool = True


class TeardownRequest(BaseModel):
    interface: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/emulation/status")
def emulation_status():
    """
    Report whether emulation is possible on this host.

    On Windows/macOS this explains what is missing instead of failing later
    with an opaque error (PRD §2.2 rule 5).
    """
    return environment_report()


@router.get("/emulation/interfaces")
def list_interfaces():
    """List interfaces that can carry a netem qdisc."""
    try:
        return {"interfaces": interface_names()}
    except EmulationUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": str(exc), "hint": exc.hint},
        ) from exc


@router.post("/emulation/config")
def apply_emulation_config(body: EmulationConfigRequest, _: RequireEmulation, db: DbSession):
    """
    Apply a tc/netem configuration and verify it (PRD §6.3).

    Operator-only and audited. Returns 503 with an actionable hint when the host
    cannot do kernel emulation, so the caller can tell "wrong platform" apart
    from "bad parameters".
    """
    cfg = TcNetemConfig(
        interface=body.interface,
        delay_ms=body.delay_ms,
        jitter_ms=body.jitter_ms,
        delay_correlation=body.delay_correlation,
        loss_rate=body.loss_rate,
        loss_correlation=body.loss_correlation,
        duplication_rate=body.duplication_rate,
        corruption_rate=body.corruption_rate,
        reorder_rate=body.reorder_rate,
        reorder_gap=body.reorder_gap,
        rate_limit_bps=body.rate_limit_bps,
        queue_depth=body.queue_depth,
        seed=body.seed,
    )

    harness = EmulationHarness(run_id=body.run_id, strict=body.strict)
    try:
        record = harness.apply(cfg, link_id=body.link_id, verify=body.verify)
    except EmulationUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": str(exc), "hint": exc.hint},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": f"tc command failed: {exc}"},
        ) from exc

    # Audit trail (PRD §7 TcConfig).
    try:
        db.add(TcConfig(
            run_id=body.run_id,
            link_id=body.link_id or "unlinked",
            qdisc_params=record.qdisc_params,
            config_hash=record.config_hash,
            applied_at=record.applied_at,
            verified=record.verified,
            verified_at=datetime.utcnow() if record.verified else None,
        ))
        db.commit()
    except Exception:
        db.rollback()

    return record.to_dict()


@router.post("/emulation/teardown")
def teardown_emulation(body: TeardownRequest, _: RequireEmulation):
    """Remove the netem qdisc from an interface."""
    try:
        remove_config(body.interface)
    except EmulationUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": str(exc), "hint": exc.hint},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": f"tc command failed: {exc}"},
        ) from exc
    return {"interface": body.interface, "removed": True}