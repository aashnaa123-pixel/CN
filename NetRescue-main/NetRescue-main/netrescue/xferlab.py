"""
netrescue/xferlab.py — the transfer lab's own experiment state.

`MultipathTransferSession` (netrescue/transfer/session.py) owns one transfer: it
chunks, schedules, retries, reassembles and hashes. What it deliberately does not
own is anything about a *workspace* — which file is loaded, what the last run
measured, what the operator has done since. That belongs here, so the transfer
engine stays a library with no opinion about HTTP or interfaces.

Three things this module is careful about, because each has a way of quietly
producing a wrong answer:

**A transfer's state belongs to that transfer.** Every chunk record, path counter
and event carries a transfer id. A second experiment cannot pick up the first
one's file, its chunks or its counters, because there is nowhere for them to be
mixed: the workspace is replaced wholesale, not merged into.

**Uploaded bytes are held, not written.** An uploaded file lives in memory with
the transfer that owns it and is dropped when that transfer is reset or expires.
Nothing is written to a path derived from anything the caller supplied, so there
is no user-controlled filename to escape and no temporary directory to sweep.

**Progress is reported, never predicted.** Chunk counts come from the session's
own event stream. If the session did not emit a delivery, no chunk is marked
delivered here, and a transfer that did not finish is reported as unfinished
however far along the bar looked a moment ago.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: Hard ceiling on an uploaded payload. Base64 inflates by 4/3, so a request body
#: can be larger than this; the limit is enforced on the *decoded* bytes.
MAX_FILE_BYTES = 8 * 1024 * 1024

#: A workspace is ephemeral. Anything older than this is dropped on the next
#: request, which bounds how long an uploaded file is held in memory.
WORKSPACE_TTL_S = 1800.0

#: Upper bound on retained event rows. A 1 MiB file at 4 KiB chunks is 256
#: chunks and several times that many events once retries are counted.
MAX_EVENTS = 4000

#: Upper bound on tracked chunk rows. Beyond this the workspace keeps the
#: aggregate counts and stops enumerating individual chunks.
MAX_TRACKED_CHUNKS = 4000

#: Server-side filename stem. The uploaded file's own name is display metadata
#: only and is never used to build a filesystem path.
SERVER_FILENAME_TEMPLATE = "upload-{transfer_id}"

SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")

TRANSFER_STATES = (
    "EMPTY",     # nothing uploaded
    "READY",     # file accepted, not started
    "RUNNING",
    "COMPLETE",  # finished and SHA-256 matched
    "FAILED",    # finished without a verified payload
    "CANCELLED",
)

CHUNK_STATES = ("PENDING", "IN_FLIGHT", "DELIVERED", "FAILED", "RETRYING")


def safe_display_name(name: str) -> str:
    """
    A filename safe to show and to echo back.

    Strips path separators and control characters. The caller still supplies this
    name, so it is treated as a label only — it never reaches the filesystem.
    """
    cleaned = SAFE_STEM.sub("_", (name or "").strip())
    cleaned = cleaned.lstrip(".") or "file"
    return cleaned[:120]


def server_filename(transfer_id: str) -> str:
    """
    The name this transfer's bytes are held under.

    Derived entirely from the generated transfer id. A caller cannot influence
    it, so there is no path to traverse and nothing to collide with another
    experiment's data.
    """
    return SERVER_FILENAME_TEMPLATE.format(transfer_id=transfer_id)


def new_transfer_id() -> str:
    return f"xfer-{uuid.uuid4().hex[:12]}"


@dataclass
class ChunkState:
    """One chunk as the receiver and the lab currently believe it to be."""

    chunk_index: int
    offset: int
    size_bytes: int
    sha256: str
    status: str = "PENDING"
    path_id: str | None = None
    attempts: int = 0
    arrival_position: int | None = None
    rtt_ms: float | None = None

    def as_dict(self) -> dict:
        return {
            "chunk_index": self.chunk_index,
            "offset": self.offset,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "status": self.status,
            "path_id": self.path_id,
            "attempts": self.attempts,
            "arrival_position": self.arrival_position,
            "rtt_ms": self.rtt_ms,
        }

    def move_to(self, status: str, counts: dict[str, int]) -> None:
        """
        Change state, keeping the aggregate counters consistent.

        `counts` is the workspace's live tally. It is passed in rather than
        reached for, so a chunk is never a second, divergent source of truth
        about how many chunks are in each state.
        """
        bucket = {
            "PENDING": "pending",
            "IN_FLIGHT": "in_flight",
            "DELIVERED": "delivered",
            "FAILED": "failed",
            "RETRYING": "retrying",
        }
        previous_bucket = bucket.get(self.status)
        next_bucket = bucket.get(status, "retrying")
        if previous_bucket == next_bucket:
            self.status = status
            return

        if previous_bucket is not None:
            counts[previous_bucket] = max(0, counts.get(previous_bucket, 0) - 1)
        counts[next_bucket] = counts.get(next_bucket, 0) + 1
        self.status = status


@dataclass
class PathStateView:
    """What the lab knows about one logical path."""

    path_id: str
    fd_estimate_ms: float
    tp_estimate_bps: float
    loss_rate: float
    status: str = "UP"
    chunks_sent: int = 0
    in_flight: int = 0
    retransmissions: int = 0

    def as_dict(self) -> dict:
        return {
            "path_id": self.path_id,
            "fd_estimate_ms": round(self.fd_estimate_ms, 3),
            "tp_estimate_bps": round(self.tp_estimate_bps, 1),
            "loss_rate": round(self.loss_rate, 5),
            "status": self.status,
            "chunks_sent": self.chunks_sent,
            "in_flight": self.in_flight,
            "retransmissions": self.retransmissions,
            # In-application logical path: a modelled delay/loss profile inside
            # this process, not a separate interface or cable.
            "kind": "logical",
        }


@dataclass
class TransferWorkspace:
    """
    One experiment's file, configuration, live progress and result.

    Replaced wholesale on upload so no state can leak between experiments. The
    reconstructed payload is held here and nowhere else.
    """

    transfer_id: str
    filename: str
    size_bytes: int
    source_sha256: str
    chunk_size_bytes: int = 4096
    total_chunks: int = 0
    scheduler_variant: str = "DB-DSP"
    retransmit_policy: str = "RTX-CL"
    max_retries: int = 3
    seed: int | None = None
    paths: list[PathStateView] = field(default_factory=list)
    chunks: dict[int, ChunkState] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=lambda: {
        "pending": 0, "in_flight": 0, "delivered": 0, "failed": 0,
        "retrying": 0, "delivered_bytes": 0,
    })
    arrival_order: list[int] = field(default_factory=list)
    out_of_order_count: int = 0
    retransmission_count: int = 0
    state: str = "READY"
    events: list[dict] = field(default_factory=list)
    result: dict | None = None
    started_at: float | None = None
    ended_at: float | None = None
    created_at: float = field(default_factory=time.time)
    #: The receiver's bytes, held only for this transfer and dropped on reset.
    reconstructed: bytes | None = None
    #: Chunk enumeration stops past this many rows; counts stay accurate.
    chunk_enumeration_truncated: bool = False

    # ------------------------------------------------------------------

    @property
    def elapsed_s(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.ended_at if self.ended_at is not None else time.time()
        return max(0.0, end - self.started_at)

    @property
    def progress_pct(self) -> float:
        if not self.total_chunks:
            return 0.0
        return round(100.0 * self.counts["delivered"] / self.total_chunks, 2)

    @property
    def goodput_bps(self) -> float:
        """Bits per second actually delivered, over the measured elapsed time."""
        if self.elapsed_s <= 0:
            return 0.0
        return round(self.counts["delivered_bytes"] * 8 / self.elapsed_s, 2)

    def server_filename(self) -> str:
        return server_filename(self.transfer_id)

    def record(self, event_type: str, message: str, severity: str = "info",
               detail: Any = None) -> dict:
        event = {
            "seq": len(self.events) + 1,
            "ts": time.time(),
            "transfer_id": self.transfer_id,
            "type": event_type,
            "message": message,
            "severity": severity,
            "detail": detail or {},
        }
        self.events.append(event)
        if len(self.events) > MAX_EVENTS:
            del self.events[:-MAX_EVENTS]
        return event

    def summary(self) -> dict:
        return {
            "transfer_id": self.transfer_id,
            "filename": self.filename,
            "server_filename": self.server_filename(),
            "file_size_bytes": self.size_bytes,
            "source_sha256": self.source_sha256,
            "chunk_size_bytes": self.chunk_size_bytes,
            "total_chunks": self.total_chunks,
            "scheduler_variant": self.scheduler_variant,
            "retransmit_policy": self.retransmit_policy,
            "max_retries": self.max_retries,
            "state": self.state,
            "counts": dict(self.counts),
            "progress_pct": self.progress_pct,
            "paths": [p.as_dict() for p in self.paths],
            "out_of_order_count": self.out_of_order_count,
            "retransmission_count": self.retransmission_count,
            "arrival_order": self.arrival_order[:2000],
            "elapsed_s": round(self.elapsed_s, 4),
            "goodput_bps": self.goodput_bps,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "chunk_enumeration_truncated": self.chunk_enumeration_truncated,
            "result": self.result,
        }

    def as_dict(self, include_chunks: bool = True,
                include_events: bool = True) -> dict:
        payload = self.summary()
        if include_chunks:
            payload["chunks"] = (
                [c.as_dict() for c in self.chunks.values()]
                if not self.chunk_enumeration_truncated
                else []
            )
        if include_events:
            payload["events"] = self.events[-300:]
        return payload

    # ------------------------------------------------------------------
    # Event ingestion
    # ------------------------------------------------------------------

    def begin(self) -> None:
        self.state = "RUNNING"
        self.started_at = time.time()
        self.ended_at = None
        self.counts = {
            "pending": self.total_chunks, "in_flight": 0, "delivered": 0,
            "failed": 0, "retrying": 0, "delivered_bytes": 0,
        }
        self.arrival_order = []
        self.out_of_order_count = 0
        self.retransmission_count = 0
        self.result = None
        self.reconstructed = None

    def observe(self, event_type: str, payload: dict) -> None:
        """
        Fold one session event into the workspace.

        Every transition here is driven by something the session actually did.
        There is no interpolation and no timer: if a chunk is marked delivered,
        the session emitted `chunk.delivered` for it.
        """
        if event_type == "chunk.assigned":
            index = payload["chunk_index"]
            self._chunk(index).move_to("IN_FLIGHT", self.counts)
            self._chunk(index).path_id = payload.get("path_id")
            self._chunk(index).attempts = payload.get("attempt", 1)
            for path in self.paths:
                if path.path_id == payload.get("path_id"):
                    path.in_flight += 1
                    break
            self.record(
                "chunk.assigned",
                f"Chunk {index} → {payload.get('path_id')} "
                f"(attempt {payload.get('attempt')}, offset {payload.get('offset')})",
                "info", payload,
            )

        elif event_type == "chunk.lost":
            index = payload["chunk_index"]
            chunk = self._chunk(index)
            chunk.move_to("RETRYING", self.counts)
            chunk.attempts = payload.get("attempt", chunk.attempts)
            self.counts["in_flight"] = max(0, self.counts["in_flight"] - 1)
            self.counts["retrying"] += 1
            self.retransmission_count += 1
            for path in self.paths:
                if path.path_id == payload.get("path_id"):
                    path.in_flight = max(0, path.in_flight - 1)
                    path.retransmissions += 1
                    break
            self.record(
                "chunk.lost",
                f"Chunk {index} lost on {payload.get('path_id')} — "
                f"retransmitting (attempt {payload.get('attempt')} of "
                f"{payload.get('max_attempts')})",
                "warning", payload,
            )

        elif event_type == "chunk.delivered":
            index = payload["chunk_index"]
            chunk = self._chunk(index)
            was_delivered = chunk.status == "DELIVERED"
            chunk.status = "DELIVERED"
            chunk.path_id = payload.get("path_id")
            chunk.attempts = payload.get("attempt", chunk.attempts)
            chunk.rtt_ms = payload.get("rtt_ms")
            chunk.arrival_position = payload.get("arrival_position")
            if not was_delivered:
                self.counts["delivered"] += 1
                self.counts["pending"] = max(0, self.counts["pending"] - 1)
                self.counts["delivered_bytes"] += chunk.size_bytes
                for path in self.paths:
                    if path.path_id == payload.get("path_id"):
                        path.chunks_sent += 1
                        break
            self.counts["in_flight"] = max(0, self.counts["in_flight"] - 1)
            self.arrival_order.append(index)
            high_water = max(self.arrival_order[:-1], default=-1)
            if index < high_water:
                self.out_of_order_count += 1
                self.record(
                    "chunk.out_of_order",
                    f"Chunk {index} arrived after chunk {high_water} — "
                    "reconstruction is by offset, not arrival order.",
                    "warning", payload,
                )
            else:
                self.record(
                    "chunk.delivered",
                    f"Chunk {index} received via {payload.get('path_id')} "
                    f"(attempt {payload.get('attempt')}, RTT "
                    f"{payload.get('rtt_ms')} ms)",
                    "info", payload,
                )

        elif event_type == "chunk.failed":
            index = payload["chunk_index"]
            self._chunk(index).move_to("FAILED", self.counts)
            self.counts["pending"] = max(0, self.counts["pending"] - 1)
            self.counts["failed"] += 1
            self.record(
                "chunk.failed",
                f"Chunk {index} FAILED after {payload.get('attempts')} attempts.",
                "error", payload,
            )

        elif event_type == "transfer.reassembled":
            self.record(
                "transfer.reassembled",
                f"Reassembled {payload.get('total_chunks')} chunks into "
                f"{payload.get('reconstructed_bytes')} bytes in index order "
                f"({payload.get('out_of_order_count')} arrived out of order).",
                "info", payload,
            )

        elif event_type in ("transfer.verified", "transfer.integrity_failed"):
            ok = bool(payload.get("integrity_ok"))
            self.record(
                "transfer.verified" if ok else "transfer.integrity_failed",
                ("SHA-256 verified: reconstructed bytes match the source."
                 if ok else
                 f"SHA-256 MISMATCH — source {payload.get('source_sha256')} vs "
                 f"received {payload.get('receiver_sha256')}"),
                "info" if ok else "error", payload,
            )

        elif event_type == "transfer.incomplete":
            self.record(
                "transfer.incomplete",
                f"{payload.get('delivered_chunks')}/{payload.get('total_chunks')} "
                "chunks delivered — the payload cannot be reconstructed and no "
                "integrity verdict is claimed.",
                "error", payload,
            )

    def finish(self, result: dict, reconstructed: bytes | None) -> None:
        """
        Record the terminal outcome once the session has finished.

        Three outcomes, kept strictly apart. An incomplete transfer is not a
        failed verification — no hash was ever computed on the receiver side, so
        reporting a mismatch would be claiming a comparison that never happened.
        """
        self.ended_at = time.time()
        self.reconstructed = reconstructed
        self.result = result

        total = result.get("total_chunks", 0)
        delivered = result.get("delivered_chunks", 0)

        if delivered < total:
            self.state = "FAILED"
            self.reconstructed = None
            self.record(
                "transfer.failed",
                f"{delivered}/{total} chunks delivered — the payload cannot be "
                "reconstructed, so no SHA-256 verdict is claimed and there is "
                "nothing to download.",
                "error", result,
            )
            return

        if result.get("integrity_ok") is True:
            self.state = "COMPLETE"
            self.record(
                "transfer.completed",
                f"Transfer complete — {delivered} chunks, SHA-256 verified, "
                f"{result.get('duration_s', 0):.2f}s.",
                "info", result,
            )
            return

        # Every chunk arrived and the hash still disagrees: a real mismatch.
        self.state = "FAILED"
        self.reconstructed = None
        self.record(
            "transfer.failed",
            f"Reconstruction completed ({delivered}/{total} chunks) but SHA-256 "
            f"did not match — source {result.get('source_sha256')} vs received "
            f"{result.get('receiver_sha256')}. The received bytes are not the "
            "source bytes.",
            "error", result,
        )

    # ------------------------------------------------------------------

    def _chunk(self, index: int) -> ChunkState:
        chunk = self.chunks.get(index)
        if chunk is None:
            size = self.chunk_size_bytes
            chunk = ChunkState(
                chunk_index=index,
                offset=index * self.chunk_size_bytes,
                size_bytes=size,
                sha256="",
            )
            if len(self.chunks) < MAX_TRACKED_CHUNKS:
                self.chunks[index] = chunk
            else:
                self.chunk_enumeration_truncated = True
        return chunk


def build_workspace(
    filename: str,
    data: bytes,
    chunk_size_bytes: int,
    paths: list[dict],
    scheduler_variant: str = "DB-DSP",
    retransmit_policy: str = "RTX-CL",
    max_retries: int = 3,
    seed: int | None = None,
) -> TransferWorkspace:
    """
    Create a workspace from an accepted upload.

    The payload's SHA-256 is computed once here — from the bytes actually
    received — so the comparison later is against what arrived, not against what
    the browser claimed it was sending.
    """
    transfer_id = new_transfer_id()
    chunk_size = max(1, int(chunk_size_bytes))
    total_chunks = (len(data) + chunk_size - 1) // chunk_size

    workspace = TransferWorkspace(
        transfer_id=transfer_id,
        filename=safe_display_name(filename),
        size_bytes=len(data),
        source_sha256=hashlib.sha256(data).hexdigest(),
        chunk_size_bytes=chunk_size,
        total_chunks=total_chunks,
        scheduler_variant=scheduler_variant,
        retransmit_policy=retransmit_policy,
        max_retries=max_retries,
        seed=seed,
        paths=[
            PathStateView(
                path_id=str(p.get("path_id") or f"path-{i + 1}"),
                fd_estimate_ms=float(p.get("fd_estimate_ms", 10.0)),
                tp_estimate_bps=float(p.get("tp_estimate_bps", 1_000_000.0)),
                loss_rate=float(p.get("loss_rate", 0.0)),
            )
            for i, p in enumerate(paths)
        ],
    )

    for index in range(min(total_chunks, MAX_TRACKED_CHUNKS)):
        offset = index * chunk_size
        workspace.chunks[index] = ChunkState(
            chunk_index=index,
            offset=offset,
            size_bytes=min(chunk_size, len(data) - offset),
            sha256=hashlib.sha256(data[offset:offset + chunk_size]).hexdigest(),
        )
    if total_chunks > MAX_TRACKED_CHUNKS:
        workspace.chunk_enumeration_truncated = True

    workspace.counts["pending"] = total_chunks
    workspace.record(
        "transfer.created",
        f"{workspace.filename} accepted — {len(data)} bytes, "
        f"{total_chunks} chunk(s) of {chunk_size} bytes.",
        "info",
        {"transfer_id": transfer_id, "file_size_bytes": len(data),
         "total_chunks": total_chunks},
    )
    return workspace