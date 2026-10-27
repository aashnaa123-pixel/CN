"""
apps/api/routers/xfer.py — Feature B's transfer lab.

  GET    /api/xfer/state              everything the lab renders
  POST   /api/xfer/upload             accept a real file and chunk it
  POST   /api/xfer/start              run the transfer, streaming progress
  POST   /api/xfer/cancel             stop a running transfer
  POST   /api/xfer/reset              drop the workspace and its held bytes
  GET    /api/xfer/download           the reconstructed payload
  GET    /api/xfer/variants           what this build supports
  POST   /api/xfer/schedulers/compare FIFO vs priority over identical arrivals
  POST   /api/xfer/variants/compare   every scheduler variant, same payload
  GET    /api/xfer/events             the experiment journal
  POST   /api/xfer/events/clear       empty the journal
  GET    /api/xfer/export             the experiment log, JSON or CSV

What is real here, and what is not
----------------------------------
Real: the file bytes the browser uploaded. The chunking. The path selection, per
whatever variant was chosen. The retransmission decisions. The arrival order the
receiver actually saw. The reassembly by offset. The SHA-256 on both sides, and
the comparison between them. The downloaded file is the same byte string the
receiver hashed.

Not real: the network. A "path" here is a modelled delay-and-loss profile inside
this one process (netrescue/transfer/session.py `_deliver`), not a separate
interface, socket pair or cable. Nothing left this machine. Every response that
could be mistaken for a measurement says so — see `delivery_model` in /state and
`simulated` in the scheduler comparison — and the scheduler comparison reuses
netrescue.traffic, whose results are queueing-model output, labelled as such
rather than presented as measurements of a wire.

The rule this router exists to hold: a transfer is reported complete only when
the reconstructed bytes hashed to the same SHA-256 as the bytes that were
uploaded. Progress reaching 100% is not evidence and is not treated as such.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import csv
import io
import json
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from apps.api.deps import RequireOperator, RequireRead
from apps.api.runtime import transfer_lab
from netrescue.benchmark.comparison import (
    ALL_RETRANSMIT_POLICIES,
    ALL_VARIANTS,
    ComparisonConfig,
    PathSpec,
    SchedulerComparisonHarness,
)
from netrescue.labs import execution
from netrescue.security.audit import default_auditor
from netrescue.traffic import LinkConfig, WorkloadConfig, run_comparison
from netrescue.traffic.scheduler import SchedulerVariant
from netrescue.transfer.scheduler import PathState
from netrescue.transfer.session import (
    MultipathTransferSession,
    RetransmitPolicy,
    SchedulerVariant as ChunkSchedulerVariant,
)
from netrescue.xferlab import (
    MAX_FILE_BYTES,
    WORKSPACE_TTL_S,
    PathStateView,
    TransferWorkspace,
    build_workspace,
)

router = APIRouter(prefix="/api/xfer", tags=["multipath-lab"])
log = logging.getLogger(__name__)

DELIVERY_MODEL = (
    "In-application logical paths. Each path is a modelled delay-and-loss "
    "profile inside this process, not a separate network interface or a physical "
    "route. Chunk scheduling, retransmission, arrival order, reassembly and "
    "SHA-256 verification are all real; the network between the endpoints is not."
)

SCHEDULER_CAVEAT = (
    "Simulated scheduler results: discrete-event queueing-model output, not a "
    "measurement of real socket traffic. Priority scheduling is best-effort and "
    "non-preemptive."
)

#: A comparison runs variants x policies x repetitions full transfers, and each
#: cell simulates a real number of path hop delays. Without a ceiling a request
#: for the full matrix can occupy the event loop for minutes — which would also
#: stall the transfer the operator is watching. The cap is refused with a reason
#: rather than silently truncating, because a truncated comparison reported as a
#: complete one is worse than a slow one.
MAX_COMPARISON_CELLS = 40
MAX_COMPARISON_PAYLOAD = 4 * 1024 * 1024


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PathIn(BaseModel):
    path_id: str = Field(..., min_length=1, max_length=64)
    fd_estimate_ms: float = Field(10.0, ge=0, le=60_000)
    tp_estimate_bps: float = Field(1_000_000.0, gt=0)
    loss_rate: float = Field(0.0, ge=0.0, le=1.0)


class UploadRequest(BaseModel):
    filename: str = Field("file", max_length=260)
    data_b64: str = Field(..., description="Base64-encoded file bytes.")
    chunk_size_bytes: int = Field(4096, ge=256, le=1 << 20)
    paths: list[PathIn] = Field(..., min_length=1, max_length=8)
    scheduler_variant: str = "DB-DSP"
    retransmit_policy: str = "RTX-CL"
    max_retries: int = Field(3, ge=0, le=20)
    seed: int | None = Field(None, ge=0, le=2**31 - 1)


class StartRequest(BaseModel):
    scheduler_variant: str | None = None
    retransmit_policy: str | None = None
    chunk_size_bytes: int | None = Field(None, ge=256, le=1 << 20)
    max_retries: int | None = Field(None, ge=0, le=20)
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    paths: list[PathIn] | None = None


class LinkIn(BaseModel):
    rate_bps: float = Field(5_000_000.0, gt=0)
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


class SchedulerCompareRequest(BaseModel):
    workload: WorkloadIn = Field(default_factory=WorkloadIn)
    link: LinkIn = Field(default_factory=LinkIn)


class VariantCompareRequest(BaseModel):
    paths: list[PathIn] = Field(..., min_length=1, max_length=8)
    payload_bytes: int = Field(120_000, ge=1024, le=MAX_COMPARISON_PAYLOAD)
    chunk_size_bytes: int = Field(4096, ge=512, le=1_048_576)
    repetitions: int = Field(3, ge=1, le=50)
    variants: list[str] = Field(default_factory=lambda: list(ALL_VARIANTS))
    retransmit_policies: list[str] = Field(
        default_factory=lambda: list(ALL_RETRANSMIT_POLICIES)
    )
    seed: int | None = Field(1337)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _audit(principal, action: str, resource: str, ok: bool, detail: dict) -> None:
    default_auditor().record(principal.name, action, resource, ok, detail)


def _workspace() -> TransferWorkspace:
    workspace = transfer_lab.active()
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No file is loaded. Choose a file and upload it first.",
        )
    return workspace


def _validate_variant(value: str) -> str:
    try:
        return ChunkSchedulerVariant(value).value
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown scheduler variant {value!r}. Supported: "
                   f"{[v.value for v in ChunkSchedulerVariant]}",
        ) from exc


def _validate_policy(value: str) -> str:
    try:
        return RetransmitPolicy(value).value
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown retransmit policy {value!r}. Supported: "
                   f"{[p.value for p in RetransmitPolicy]}",
        ) from exc


def _state_payload(workspace: TransferWorkspace | None) -> dict:
    """
    One consistent read of the lab.

    Node states, chunk states, paths and the event journal are assembled in a
    single place so the canvas, the counters and the timeline cannot be drawn
    from two different moments and disagree.
    """
    payload: dict[str, Any] = {
        "transfer": None,
        "chunks": [],
        "events": [],
        "delivery_model": DELIVERY_MODEL,
        "max_file_bytes": MAX_FILE_BYTES,
        "workspace_ttl_s": WORKSPACE_TTL_S,
        "comparisons": transfer_lab.comparisons[-5:],
        "state": "EMPTY" if workspace is None else workspace.state,
        "running": bool(workspace and workspace.state == "RUNNING"),
    }
    execution.attach(payload, "transfer.multipath",
                     outcome="paths are modelled in-process; the file bytes and SHA-256 are real")
    if workspace is None:
        return payload

    payload["transfer"] = workspace.summary()
    payload["chunks"] = (
        [c.as_dict() for c in workspace.chunks.values()]
        if not workspace.chunk_enumeration_truncated else []
    )
    payload["events"] = workspace.events[-250:]
    payload["download_available"] = (
        workspace.state == "COMPLETE" and workspace.reconstructed is not None
    )
    return payload


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@router.get("/state")
def get_state(_: RequireRead):
    """
    Everything the lab renders. Always 200.

    An empty workspace is a normal state for a screen that renders an empty view,
    not an error.
    """
    return _state_payload(transfer_lab.active())


@router.get("/variants")
def list_variants(_: RequireRead):
    """
    What this build can run, read from the implementations rather than hardcoded.

    An option can therefore never appear in the interface that the backend would
    reject, and a variant that is not implemented is absent rather than silently
    substituted for another one.
    """
    return {
        "scheduler_variants": [
            {"id": v.value, "label": v.label} for v in ChunkSchedulerVariant
        ],
        "retransmit_policies": [
            {"id": p.value, "label": p.label} for p in RetransmitPolicy
        ],
        "link_schedulers": [
            {"id": "fifo", "label": "FIFO (baseline)",
             "description": "Serves strictly in arrival order and ignores traffic class."},
            {"id": "priority", "label": "Priority",
             "description": "Critical traffic before bulk; bulk fills idle capacity. "
                            "Best-effort and non-preemptive."},
        ],
        "limits": {
            "max_file_bytes": MAX_FILE_BYTES,
            "max_paths": 8,
            "chunk_size_range": [256, 1 << 20],
        },
        "delivery_model": DELIVERY_MODEL,
        "scheduler_caveat": SCHEDULER_CAVEAT,
    }


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@router.post("/upload")
async def upload(body: UploadRequest, principal: RequireOperator):
    """
    Accept a real file, chunk it, and hold it for this experiment only.

    The SHA-256 recorded here is computed from the bytes that actually arrived,
    not from anything the browser asserted about them — so the comparison at the
    end of the transfer is against what was received.

    The payload is never written to disk. There is no path built from the
    caller's filename, so there is nothing for a crafted name to traverse, and
    nothing to clean up beyond dropping the workspace.
    """
    try:
        data = base64.b64decode(body.data_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="data_b64 is not valid base64.",
        ) from exc

    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The selected file is empty, so there is nothing to transfer.",
        )
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"The file is {len(data)} bytes; the limit is "
                   f"{MAX_FILE_BYTES} bytes ({MAX_FILE_BYTES // (1024 * 1024)} MiB).",
        )

    variant = _validate_variant(body.scheduler_variant)
    policy = _validate_policy(body.retransmit_policy)

    if len(body.paths) < 2 and variant not in (
        ChunkSchedulerVariant.SINGLE_PATH.value,
        ChunkSchedulerVariant.NAIVE_DELAY.value,
    ):
        log.info("[xfer] %s: %s selected with a single path", body.filename, variant)

    workspace = build_workspace(
        filename=body.filename,
        data=data,
        chunk_size_bytes=body.chunk_size_bytes,
        paths=[p.model_dump() for p in body.paths],
        scheduler_variant=variant,
        retransmit_policy=policy,
        max_retries=body.max_retries,
        seed=body.seed,
    )
    transfer_lab.install(workspace, data)

    _audit(principal, "xfer.upload", workspace.transfer_id, True,
           {"filename": workspace.filename, "bytes": len(data),
            "total_chunks": workspace.total_chunks})
    return _state_payload(workspace)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


@router.post("/start")
async def start(body: StartRequest, principal: RequireOperator):
    """
    Run the transfer and stream its progress into the workspace.

    Returns as soon as the run is underway; the browser polls /state for chunk
    progress. Nothing about the result is predicted here — the state this starts
    with is "RUNNING" and nothing more.
    """
    workspace = _workspace()
    if workspace.state == "RUNNING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Transfer {workspace.transfer_id} is already running. "
                   "Wait for it to finish, or cancel it first.",
        )

    if body.paths is not None:
        # Rebuild the path list wholesale so no counter from the previous
        # configuration survives into this run.
        workspace.paths = [
            PathStateView(
                path_id=p.path_id,
                fd_estimate_ms=p.fd_estimate_ms,
                tp_estimate_bps=p.tp_estimate_bps,
                loss_rate=p.loss_rate,
            )
            for p in body.paths
        ]

    if body.scheduler_variant:
        workspace.scheduler_variant = _validate_variant(body.scheduler_variant)
    if body.retransmit_policy:
        workspace.retransmit_policy = _validate_policy(body.retransmit_policy)
    if body.chunk_size_bytes:
        workspace.chunk_size_bytes = body.chunk_size_bytes
        workspace.total_chunks = (
            workspace.size_bytes + body.chunk_size_bytes - 1
        ) // body.chunk_size_bytes
        workspace.chunks.clear()
    if body.max_retries is not None:
        workspace.max_retries = body.max_retries
    if body.seed is not None:
        workspace.seed = body.seed

    workspace.begin()
    workspace.record(
        "transfer.started",
        f"Starting {workspace.scheduler_variant} with {workspace.retransmit_policy} "
        f"over {len(workspace.paths)} logical path(s), "
        f"{workspace.total_chunks} chunk(s) of {workspace.chunk_size_bytes} bytes.",
        "info",
        {"scheduler_variant": workspace.scheduler_variant,
         "retransmit_policy": workspace.retransmit_policy,
         "max_retries": workspace.max_retries},
    )

    session = MultipathTransferSession(
        run_id=f"xferlab-{workspace.transfer_id}",
        transfer_id=workspace.transfer_id,
        paths=[
            PathState(
                path_id=p.path_id,
                fd_estimate_ms=p.fd_estimate_ms,
                tp_estimate_bps=p.tp_estimate_bps,
                loss_rate=p.loss_rate,
            )
            for p in workspace.paths
        ],
        chunk_size_bytes=workspace.chunk_size_bytes,
        scheduler_variant=workspace.scheduler_variant,
        max_retries=workspace.max_retries,
        retransmit_policy=workspace.retransmit_policy,
        seed=workspace.seed,
        on_event=workspace.observe,
    )

    async def run() -> None:
        """Drive one session, folding its outcome into the workspace."""
        try:
            data = transfer_lab.data_for(workspace.transfer_id)
            if data is None:
                workspace.state = "FAILED"
                workspace.ended_at = time.time()
                workspace.record(
                    "transfer.failed",
                    "The uploaded bytes are no longer held for this transfer, so "
                    "there is nothing to send.",
                    "error",
                )
                return

            result = await session.transfer(data=data)
            summary = {
                "transfer_id": result.transfer_id,
                "scheduler_variant": result.scheduler_variant,
                "retransmit_policy": result.retransmit_policy,
                "total_chunks": result.total_chunks,
                "delivered_chunks": result.delivered_chunks,
                "failed_chunks": result.failed_chunks,
                "duration_s": round(result.duration_s, 6),
                "goodput_bps": round(result.goodput_bps, 2),
                "retransmission_count": result.retransmission_count,
                "out_of_order_count": result.out_of_order_count,
                "source_sha256": result.source_sha256,
                "receiver_sha256": result.receiver_sha256,
                "integrity_ok": result.integrity_ok,
                "per_path_utilisation": result.per_path_utilisation,
                "per_path_retransmissions": result.per_path_retransmissions,
            }
            workspace.finish(summary, session.reassembled_bytes)
        except asyncio.CancelledError:
            workspace.state = "CANCELLED"
            workspace.ended_at = time.time()
            workspace.record(
                "transfer.cancelled",
                "Transfer cancelled. No integrity verdict is claimed for an "
                "interrupted transfer.",
                "warning",
            )
            raise
        except Exception as exc:
            log.exception("[%s] transfer failed", workspace.transfer_id)
            workspace.state = "FAILED"
            workspace.ended_at = time.time()
            workspace.record(
                "transfer.failed",
                f"Transfer raised {type(exc).__name__}: {exc}",
                "error",
            )

    task = asyncio.create_task(run())
    transfer_lab.attach_task(workspace.transfer_id, task)

    _audit(principal, "xfer.start", workspace.transfer_id, True,
           {"scheduler_variant": workspace.scheduler_variant,
            "total_chunks": workspace.total_chunks})
    return _state_payload(workspace)


@router.post("/cancel")
async def cancel(principal: RequireOperator):
    """
    Stop a running transfer.

    A cancelled transfer is reported as cancelled, never as complete: the chunks
    that never arrived are still missing, and there is no reconstructed payload.
    """
    workspace = _workspace()
    cancelled = await transfer_lab.cancel(workspace.transfer_id)
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nothing is running to cancel.",
        )
    _audit(principal, "xfer.cancel", workspace.transfer_id, False,
           {"transfer_id": workspace.transfer_id})
    return _state_payload(workspace)


@router.post("/reset")
def reset(principal: RequireOperator):
    """
    Drop the workspace and the bytes it held.

    This is the cleanup path: nothing survives it, so an uploaded file cannot
    outlive its experiment.
    """
    previous = transfer_lab.active()
    transfer_lab.reset()
    _audit(principal, "xfer.reset", previous.transfer_id if previous else "none", True,
           {"dropped_transfer": previous.transfer_id if previous else None})
    return _state_payload(None)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


@router.get("/download")
def download(_: RequireRead):
    """
    The reconstructed payload — the same byte string the receiver hashed.

    Only available for a transfer that finished with a verified SHA-256 match. A
    transfer that is still running, was cancelled, or failed its check returns
    409: there is nothing here that could honestly be called the received file.
    """
    workspace = _workspace()
    if workspace.state != "COMPLETE" or workspace.reconstructed is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "There is no verified file to download. This transfer is "
                f"{workspace.state}; a download is offered only after every "
                "chunk was delivered and the reconstructed SHA-256 matched the "
                "source."
            ),
        )

    payload = workspace.reconstructed
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            # The name is server-generated from the transfer id, so a crafted
            # upload name cannot influence this header.
            "Content-Disposition": f'attachment; filename="{workspace.server_filename()}"',
            "X-NetRescue-Source-Sha256": workspace.source_sha256,
            "X-NetRescue-Reconstructed-Sha256": workspace.result["receiver_sha256"],
            "X-NetRescue-Transfer-Id": workspace.transfer_id,
            "X-NetRescue-Chunks": str(workspace.total_chunks),
        },
    )


# ---------------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------------


@router.post("/schedulers/compare")
async def schedulers_compare(body: SchedulerCompareRequest, principal: RequireOperator):
    """
    FIFO versus priority over byte-identical arrivals.

    The workload is seeded, so both schedulers face the same packets in the same
    order and any difference is attributable to scheduling rather than to
    sampling noise. Each run gets a fresh scheduler, so the second cannot inherit
    queued packets or link state from the first.

    These are queueing-model results and are labelled `simulated: true` — not a
    measurement of real socket traffic.
    """
    workload = WorkloadConfig(**body.workload.model_dump())
    link = LinkConfig(**body.link.model_dump())
    link.max_queue = body.workload.max_queue

    report = run_comparison(
        transfer_lab.next_run_id(), workload, link,
        variants=(SchedulerVariant.FIFO, SchedulerVariant.PRIORITY),
    )
    payload = report.to_dict()
    payload["offered_bps"] = workload.offered_bps()
    payload["link_capacity_bps"] = link.rate_bps
    payload["congested"] = workload.offered_bps() > link.rate_bps
    payload["simulated"] = True
    payload["caveat"] = SCHEDULER_CAVEAT
    payload["table"] = report.table()
    payload["measured_over_real_sockets"] = False

    transfer_lab.record_comparison("schedulers", payload)
    _audit(principal, "xfer.schedulers.compare", "fifo|priority", True,
           {"congested": payload["congested"]})
    return execution.attach(payload, "transfer.scheduler_compare", seed=body.workload.seed)


@router.post("/variants/compare")
async def variants_compare(body: VariantCompareRequest, principal: RequireOperator):
    """
    Every chunk-scheduler variant over one identical payload, repeated.

    Uses the same payload bytes for each variant, so a difference is the
    scheduler's doing. Reports median and p95 across repetitions.

    Loss here is the transfer session's modelled per-path loss, not a real lossy
    network — the report says so, and names what was actually varied.
    """
    unknown = set(body.variants) - set(ALL_VARIANTS)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown scheduler variant(s): {sorted(unknown)}. "
                   f"Valid: {ALL_VARIANTS}",
        )
    unknown_policies = set(body.retransmit_policies) - set(ALL_RETRANSMIT_POLICIES)
    if unknown_policies:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown retransmit policy(ies): {sorted(unknown_policies)}. "
                   f"Valid: {ALL_RETRANSMIT_POLICIES}",
        )

    cells = len(body.variants) * len(body.retransmit_policies) * body.repetitions
    if cells > MAX_COMPARISON_CELLS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"That is {cells} full transfers "
                f"({len(body.variants)} variants x "
                f"{len(body.retransmit_policies)} policies x "
                f"{body.repetitions} repetitions), and the limit is "
                f"{MAX_COMPARISON_CELLS}. Narrow the variants, or lower the "
                "repetitions. The comparison was not run, and nothing partial "
                "is reported."
            ),
        )

    cfg = ComparisonConfig(
        run_id=transfer_lab.next_run_id(),
        paths=[
            PathSpec(
                path_id=p.path_id, fd_ms=p.fd_estimate_ms,
                tp_bps=p.tp_estimate_bps, loss_rate=p.loss_rate,
            )
            for p in body.paths
        ],
        payload_bytes=body.payload_bytes,
        chunk_size_bytes=body.chunk_size_bytes,
        repetitions=body.repetitions,
        variants=body.variants,
        retransmit_policies=body.retransmit_policies,
        seed=body.seed,
    )
    report = await SchedulerComparisonHarness(cfg).run()
    payload = report.to_dict()
    payload["simulated"] = True
    payload["measured_over_real_sockets"] = False
    payload["caveat"] = (
        "Loss and delay are the transfer session's modelled per-path profiles, "
        "not an impaired real network. Every variant transferred byte-identical "
        "payload bytes across the same repetitions."
    )

    transfer_lab.record_comparison("variants", payload)
    _audit(principal, "xfer.variants.compare", "all", True,
           {"variants": body.variants, "repetitions": body.repetitions})
    return execution.attach({**payload, "table": report.table(), "csv": report.to_csv()},
                            "transfer.multipath", seed=body.seed,
                            outcome="scheduler variants compared over modelled paths")


# ---------------------------------------------------------------------------
# Journal and export
# ---------------------------------------------------------------------------


@router.get("/events")
def get_events(_: RequireRead, limit: int = 400):
    """
    The experiment journal — only what the backend actually produced.

    A workspace with nothing loaded still returns 200 with an empty list: "no
    experiment has run" is a state, not a missing resource.
    """
    workspace = transfer_lab.active()
    events = workspace.events[-min(max(limit, 1), 2000):] if workspace else []
    return {
        "events": events,
        "count": len(workspace.events) if workspace else 0,
        "transfer_id": workspace.transfer_id if workspace else None,
    }


@router.post("/events/clear")
def clear_events(principal: RequireOperator):
    """Empty the journal. The transfer itself keeps running."""
    workspace = _workspace()
    workspace.events = []
    workspace.record("events.cleared", "Event timeline cleared.", "info")
    _audit(principal, "xfer.events.clear", workspace.transfer_id, True, {})
    return _state_payload(workspace)


@router.get("/export")
def export(_: RequireRead, fmt: Literal["json", "csv"] = "json"):
    """
    The experiment log as a download.

    Carries the configuration, the measured result, the SHA-256 pair, the per-path
    breakdown and every event the backend recorded. CSV is the journal flattened
    to one row per event; JSON is the whole experiment.
    """
    workspace = transfer_lab.active()
    comparisons = transfer_lab.comparisons

    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["seq", "iso_time", "transfer_id", "type", "severity",
                         "message", "detail"])
        for event in (workspace.events if workspace else []):
            writer.writerow([
                event["seq"],
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(event["ts"])),
                event["transfer_id"], event["type"], event["severity"],
                event["message"], json.dumps(event["detail"], default=str),
            ])
        return {
            "filename": "netrescue-multipath-events.csv",
            "content": buffer.getvalue(),
            "rows": len(workspace.events) if workspace else 0,
        }

    document = {
        "generated_at": time.time(),
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "delivery_model": DELIVERY_MODEL,
        "scheduler_caveat": SCHEDULER_CAVEAT,
        "transfer": workspace.as_dict() if workspace else None,
        "comparisons": comparisons,
    }
    return {
        "filename": "netrescue-multipath-experiment.json",
        "content": json.dumps(document, indent=2, default=str),
        "events": len(workspace.events) if workspace else 0,
    }