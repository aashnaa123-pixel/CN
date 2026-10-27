"""
apps/api/routers/transfers.py — Multipath transfer: run one, and inspect it.

  POST /api/transfers              run a transfer over the configured paths
  GET  /api/transfers/{id}         full result, including per-path detail
  GET  /api/transfers              recent transfers, newest first

What this endpoint will not do
------------------------------
It returns `integrity_ok` straight from the SHA-256 comparison the session
performed. It never reports a completed transfer whose hashes disagree, and it
never reports success for a file that failed to reassemble. A transfer whose
receiver hash is missing comes back `integrity_ok: null` with a status that says
so — not as a success.

The database is optional. With PostgreSQL down the transfer still runs and its
real result is still returned, with `persisted: false` and a `persistence_error`
explaining why it was not stored. Failing the whole request would hide a result
the caller legitimately earned.
"""

from __future__ import annotations

import base64
import binascii
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from apps.api.deps import OptionalDbSession, RequireOperator, RequireRead
from netrescue.core.db import DatabaseUnavailable
from netrescue.core.models import Chunk, Transfer
from netrescue.transfer.scheduler import PathState
from netrescue.transfer.session import MultipathTransferSession, SchedulerVariant
from netrescue.security.audit import default_auditor
router = APIRouter(prefix="/api", tags=["transfers"])
log = logging.getLogger(__name__)

#: Refuse absurd payloads outright rather than base64-decoding them into memory.
MAX_BYTES = 64 * 1024 * 1024

#: Node id the transfer session records as source and destination.
LOCAL_NODE_ID = "local"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PathConfig(BaseModel):
    path_id: str = Field(..., min_length=1, max_length=64)
    fd_estimate_ms: float = Field(10.0, ge=0, le=60_000)
    tp_estimate_bps: float = Field(1_000_000.0, gt=0)
    loss_rate: float = Field(0.0, ge=0.0, le=1.0)


class TransferCreate(BaseModel):
    run_id: str = Field("manual", max_length=64)
    data_b64: str = Field(..., description="Base64-encoded file bytes.")
    scheduler_variant: str = "DB-DSP"
    chunk_size_bytes: int = Field(4096, ge=64, le=1 << 20)
    max_retries: int = Field(3, ge=0, le=20)
    paths: list[PathConfig] = Field(..., min_length=1, max_length=8)


class PathStats(BaseModel):
    path_id: str
    chunks_sent: int
    retransmissions: int
    share_pct: float


class TransferOut(BaseModel):
    transfer_id: str
    run_id: str
    scheduler_variant: str
    total_chunks: int
    delivered_chunks: int
    failed_chunks: int
    status: str
    integrity_ok: Optional[bool]
    source_sha256: Optional[str]
    receiver_sha256: Optional[str]
    duration_s: float
    goodput_bps: float
    retransmission_count: int
    #: Null for a transfer read back from the database: arrival order is not
    #: stored, and reporting 0 there would claim a measurement never made.
    out_of_order_count: Optional[int] = None
    per_path: list[PathStats]
    persisted: bool
    persistence_error: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/transfers/variants")
def list_variants(_: RequireRead):
    """Schedulers and retransmit policies this build supports.

    The UI reads this rather than hardcoding, so an option can never appear in
    the interface that the backend would reject.
    """
    return {
        "scheduler_variants": [
            {"id": v.value, "label": v.label} for v in SchedulerVariant
        ],
        "retransmit_policies": [
            {"id": "RTX_CL", "label": "Cumulative loss (RTX-CL)"},
            {"id": "RTX_CWND", "label": "Cumulative bytes (RTX-CWND)"},
        ],
    }


@router.post("/transfers", response_model=TransferOut, status_code=status.HTTP_201_CREATED)
async def initiate_transfer(body: TransferCreate, principal: RequireOperator,
                            db: OptionalDbSession):
    """
    Run a multipath transfer and report exactly what happened.

    Chunks are scheduled across the supplied paths, retried up to
    `max_retries` times each, and the reassembled payload is checked against the
    source with SHA-256. The reported status and integrity verdict come from
    that comparison, not from the absence of an exception.
    """
    if len(body.paths) < 2 and body.scheduler_variant.upper() not in (
        SchedulerVariant.SINGLE_PATH.value,
        SchedulerVariant.NAIVE_DELAY.value,
    ):
        log.info(
            "transfer %s uses a multipath scheduler with a single path",
            body.scheduler_variant,
        )

    try:
        data = base64.b64decode(body.data_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="data_b64 is not valid base64.",
        ) from exc

    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"payload is {len(data)} bytes; the limit is {MAX_BYTES}.",
        )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="refusing to transfer an empty file.",
        )

    transfer_id = str(uuid.uuid4())
    _ensure_run(db, body.run_id)
    paths = [
        PathState(
            path_id=p.path_id,
            fd_estimate_ms=p.fd_estimate_ms,
            tp_estimate_bps=p.tp_estimate_bps,
            loss_rate=p.loss_rate,
        )
        for p in body.paths
    ]

    session = MultipathTransferSession(
        run_id=body.run_id,
        transfer_id=transfer_id,
        paths=paths,
        chunk_size_bytes=body.chunk_size_bytes,
        scheduler_variant=body.scheduler_variant,
        max_retries=body.max_retries,
        db_session=db,
    )

    result = await session.transfer(data=data)

    per_path = _path_stats(result)
    payload = {
        "transfer_id": transfer_id,
        "run_id": body.run_id,
        "scheduler_variant": body.scheduler_variant,
        "total_chunks": result.total_chunks,
        "delivered_chunks": result.delivered_chunks,
        "failed_chunks": result.failed_chunks,
        "status": _status_for(result),
        "integrity_ok": result.integrity_ok,
        "source_sha256": result.source_sha256,
        "receiver_sha256": result.receiver_sha256,
        "duration_s": round(result.duration_s, 6),
        "goodput_bps": round(result.goodput_bps, 2),
        "retransmission_count": result.retransmission_count,
        "out_of_order_count": result.out_of_order_count,
        "per_path": per_path,
    }

    # The session wrote the row itself; this only reports whether it landed.
    persisted, error = _persisted_state(db, transfer_id)
    payload["persisted"] = persisted
    if error:
        payload["persistence_error"] = error

    default_auditor().record(
        principal.name, "transfer.initiate", transfer_id, result.integrity_ok,
        {
            "scheduler": body.scheduler_variant,
            "bytes": len(data),
            "integrity_ok": result.integrity_ok,
            "persisted": persisted,
        },
    )
    return payload


@router.get("/transfers", response_model=list[TransferOut])
def list_transfers(db: OptionalDbSession, _: RequireRead, limit: int = 25):
    """
    Recent transfers, newest first.

    Requires PostgreSQL: there is no in-memory record of past transfers, and
    returning an empty list here would misrepresent that as "no transfers have
    run" rather than "history is unavailable".
    """
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Transfer history needs PostgreSQL. It is not running. "
                   "Individual transfers still work without it; they just are "
                   "not retained.",
        )
    rows = (
        db.query(Transfer)
        .order_by(Transfer.started_at.desc())
        .limit(min(limit, 200))
        .all()
    )
    return [_row_out(db, t) for t in rows]


@router.get("/transfers/{transfer_id}")
def get_transfer(transfer_id: str, db: OptionalDbSession, _: RequireRead):
    """One transfer with its per-chunk breakdown."""
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Looking up a past transfer needs PostgreSQL, which is not "
                   "running.",
        )
    xfer = db.query(Transfer).filter(Transfer.transfer_id == transfer_id).first()
    if not xfer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transfer {transfer_id!r} not found. It may have run while "
                   f"the database was unavailable, in which case it was not kept.",
        )
    chunks = (
        db.query(Chunk)
        .filter(Chunk.transfer_id == transfer_id)
        .order_by(Chunk.chunk_index)
        .all()
    )
    return {**_row_out(db, xfer), "chunks": [
        {
            "chunk_index": c.chunk_index,
            "status": c.status,
            "path_id": c.path_id,
            "attempts": c.attempts,
        }
        for c in chunks
    ]}


def _row_out(db, t) -> dict:
    """
    Render a stored transfer.

    The `transfers` table stores the transfer's identity and its integrity
    verdict; the per-chunk detail lives in `chunks`. Rather than add derived
    columns that would need a migration, the counts, duration and goodput are
    recomputed from the chunk rows that were actually written.

    `out_of_order_count` is arrival order, which is not recoverable from a stored
    transfer. It is reported as null rather than as zero, because "we did not
    measure this after the fact" and "we measured no disorder" are different
    claims.
    """
    chunks = db.query(Chunk).filter(Chunk.transfer_id == t.transfer_id).all()
    delivered = [c for c in chunks if c.status == "DELIVERED"]
    failed = [c for c in chunks if c.status == "FAILED"]
    attempts = sum(c.attempts or 0 for c in chunks)
    retries = max(0, attempts - len(chunks)) if chunks else 0

    per_path_counts: dict[str, int] = {}
    for c in delivered:
        per_path_counts[c.path_id or "unassigned"] = (
            per_path_counts.get(c.path_id or "unassigned", 0) + 1
        )
    total_sent = sum(per_path_counts.values())
    per_path = [
        {
            "path_id": path_id,
            "chunks_sent": count,
            "retransmissions": 0,
            "share_pct": round(100.0 * count / total_sent, 2) if total_sent else 0.0,
        }
        for path_id, count in sorted(per_path_counts.items())
    ]

    duration = 0.0
    if t.started_at and t.ended_at:
        duration = max((t.ended_at - t.started_at).total_seconds(), 0.0)

    return {
        "transfer_id": t.transfer_id,
        "run_id": t.run_id,
        "scheduler_variant": t.scheduler_variant,
        "total_chunks": t.total_chunks,
        "delivered_chunks": len(delivered),
        "failed_chunks": len(failed),
        "status": t.status,
        "integrity_ok": t.integrity_ok,
        "source_sha256": t.source_sha256,
        "receiver_sha256": t.receiver_sha256,
        "duration_s": round(duration, 6),
        "goodput_bps": round(t.file_size_bytes * 8 / duration, 2) if duration > 0 else 0.0,
        "retransmission_count": retries,
        "out_of_order_count": None,
        "per_path": per_path,
        "persisted": True,
        "started_at": t.started_at,
        "ended_at": t.ended_at,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _status_for(result) -> str:
    """
    Derive the status from the integrity verdict, not from progress.

    A transfer that moved every byte but failed its hash check is FAILED, and one
    that has not finished hashing has no integrity verdict at all.
    """
    if result.integrity_ok:
        return "COMPLETE"
    if result.receiver_sha256 is None:
        return "INCOMPLETE"
    return "INTEGRITY_FAILED"


def _path_stats(result) -> list[PathStats]:
    util = result.per_path_utilisation or {}
    retrans = result.per_path_retransmissions or {}
    total = sum(util.values())
    out = []
    for path_id in sorted(set(util) | set(retrans)):
        sent = util.get(path_id, 0)
        out.append(PathStats(
            path_id=path_id,
            chunks_sent=sent,
            retransmissions=retrans.get(path_id, 0),
            share_pct=round(100.0 * sent / total, 2) if total else 0.0,
        ))
    return out


def _ensure_run(db, run_id: str) -> None:
    """
    Make sure the parent rows a transfer depends on exist.

    `transfers.run_id` and `transfers.source_node_id`/`dest_node_id` are foreign
    keys. A transfer launched straight from the Transfer Lab has no experiment
    and no registered node, so it would otherwise fail to persist on a constraint
    that has nothing to do with the transfer itself. Creating minimal parent rows
    is the honest fix: the run genuinely happened, and this records it.
    """
    if db is None:
        return
    from netrescue.core.models import Experiment, Node

    try:
        if not db.query(Experiment).filter(Experiment.id == run_id).first():
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            db.add(Experiment(
                id=run_id,
                feature="C",
                status="DONE",
                started_at=now,
                ended_at=now,
                config={"origin": "transfer-lab"},
            ))
        # The session records a local-to-local transfer; that node must exist.
        if not db.query(Node).filter(Node.node_id == LOCAL_NODE_ID).first():
            db.add(Node(
                node_id=LOCAL_NODE_ID,
                status="UP",
                software_version="lab",
            ))
        db.commit()
    except Exception as exc:
        db.rollback()
        log.warning("could not create parent rows for run %s: %s", run_id, exc)


def _persisted_state(db, transfer_id: str) -> tuple[bool, str | None]:
    """
    Report whether the session's own write landed.

    MultipathTransferSession persists the transfer and its chunks itself, and
    deliberately swallows persistence errors so a database fault never loses a
    measured result. This does not re-insert anything — it only checks whether
    the row is there, so the caller can say honestly whether the result was
    stored or only returned.
    """
    if db is None:
        return False, (
            "PostgreSQL is not running, so this transfer was measured but not "
            "stored. The result shown is still real; it will not appear in "
            "history."
        )
    try:
        row = db.query(Transfer).filter(Transfer.transfer_id == transfer_id).first()
        if row is None:
            return False, (
                "The transfer ran and its result is shown above, but it was not "
                "written to the database, so it will not appear in run history."
            )
        return True, None
    except Exception as exc:
        db.rollback()
        return False, f"Could not confirm persistence: {exc}"
