"""
netrescue/transfer/session.py

MultipathTransferSession — orchestrates a full multipath file transfer.

Pipeline (PRD §5.3, §5.4):
  1. Split the payload into fixed-size chunks; compute a per-chunk SHA-256.
  2. Schedule each chunk through the selected scheduler variant.
  3. Dispatch chunks **concurrently** through a bounded in-flight window, so the
     whole point of multipath is actually exercised. Each path carries a
     simulated delay/loss profile.
  4. On loss, retransmit via the configured RTX policy (RTX-CL or the RTX-CWND
     baseline), with bounded retries.
  5. Reassemble in index order and verify full-file SHA-256.
  6. Persist Transfer + Chunk rows (PRD §7).

Out-of-order accounting
-----------------------
Chunks are delivered asynchronously, so the receiver observes a real arrival
order. `out_of_order_count` is the number of chunks that arrived after a
higher-indexed chunk had already arrived — the standard reordering-event metric
and the one PRD §11 compares DB-DSP against round-robin on.

Note the arrival order is recorded at the *destination*, after the simulated
per-path delay, not in dispatch order. Dispatch order alone would always be
monotonic and would report zero reordering for every variant.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

from netrescue.transfer.scheduler import DBDSPScheduler, PathState, PathStatus
from netrescue.transfer.variants import (
    BaseScheduler,
    NaiveDelayScheduler,
    RoundRobinScheduler,
    RtxClPolicy,
    RtxCwndPolicy,
    SinglePathScheduler,
    WeightedRoundRobinScheduler,
)

log = logging.getLogger(__name__)

_DEFAULT_MAX_RETRIES = 3

#: Minimum throughput sentinel, matching scheduler._TP_MIN_BPS, so a path can
#: never divide by zero and the two cost models stay identical.
_TP_MIN_BPS = 1.0

#: Default number of chunks allowed in flight at once. Bounds memory and keeps
#: the send window wider than any single path can drain, so multipath helps.
DEFAULT_IN_FLIGHT = 16

#: Per-path cap on concurrent in-flight chunks. Left generous: it bounds how
#: much work one path can absorb, and must stay large enough that a path's
#: transmission time — not this cap — is what limits throughput.
DEFAULT_PER_PATH_IN_FLIGHT = 8


class SchedulerVariant(str, Enum):
    DB_DSP = "DB-DSP"
    ROUND_ROBIN = "RR"
    WEIGHTED_RR = "WRR"
    SINGLE_PATH = "SINGLE"
    NAIVE_DELAY = "NAIVE_DELAY"

    @property
    def label(self) -> str:
        return {
            SchedulerVariant.DB_DSP: "DB-DSP (primary)",
            SchedulerVariant.ROUND_ROBIN: "Round-Robin",
            SchedulerVariant.WEIGHTED_RR: "Weighted Round-Robin",
            SchedulerVariant.SINGLE_PATH: "Single-Path TCP",
            SchedulerVariant.NAIVE_DELAY: "Naïve Delay-Aware",
        }[self]


class RetransmitPolicy(str, Enum):
    RTX_CL = "RTX-CL"
    RTX_CWND = "RTX-CWND"

    @property
    def label(self) -> str:
        return {
            RetransmitPolicy.RTX_CL: "RTX-CL — delay and loss aware",
            RetransmitPolicy.RTX_CWND: "RTX-CWND — congestion-window baseline",
        }[self]


@dataclass
class ChunkRecord:
    chunk_index: int
    total_chunks: int
    data: bytes
    sha256: str
    #: Byte offset of this chunk in the original payload. Reconstruction is by
    #: offset, never by arrival order.
    offset: int = 0
    path_id: str | None = None
    attempts: int = 0
    status: str = "PENDING"  # PENDING | IN_FLIGHT | DELIVERED | FAILED


@dataclass
class TransferResult:
    transfer_id: str
    run_id: str
    scheduler_variant: str
    total_chunks: int
    delivered_chunks: int
    failed_chunks: int
    source_sha256: str
    receiver_sha256: str | None
    integrity_ok: bool
    started_at: datetime
    ended_at: datetime
    duration_s: float
    out_of_order_count: int
    retransmission_count: int
    per_path_utilisation: dict[str, int]
    # --- added for the comparison harness (PRD §6.5) ---
    retransmit_policy: str = RetransmitPolicy.RTX_CL.value
    goodput_bps: float = 0.0
    arrival_order: list[int] = field(default_factory=list)
    per_path_retransmissions: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict:
        return {
            "transfer_id": self.transfer_id,
            "scheduler_variant": self.scheduler_variant,
            "retransmit_policy": self.retransmit_policy,
            "total_chunks": self.total_chunks,
            "delivered_chunks": self.delivered_chunks,
            "failed_chunks": self.failed_chunks,
            "integrity_ok": self.integrity_ok,
            "duration_s": round(self.duration_s, 4),
            "out_of_order_count": self.out_of_order_count,
            "retransmission_count": self.retransmission_count,
            "goodput_bps": round(self.goodput_bps, 1),
            "per_path_utilisation": self.per_path_utilisation,
        }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _chunk_data(data: bytes, chunk_size: int) -> list[bytes]:
    return [data[i: i + chunk_size] for i in range(0, len(data), chunk_size)]


class MultipathTransferSession:
    """
    Orchestrates a multipath transfer with a pluggable scheduler variant.

    Usage::

        paths = [PathState("p1", fd_estimate_ms=10),
                 PathState("p2", fd_estimate_ms=50),
                 PathState("p3", fd_estimate_ms=100)]
        session = MultipathTransferSession(
            run_id="run-1", transfer_id="xfer-1", paths=paths,
            scheduler_variant="DB-DSP", retransmit_policy="RTX-CL",
        )
        result = asyncio.run(session.transfer(b"..."))
        assert result.integrity_ok
    """

    def __init__(
        self,
        run_id: str,
        transfer_id: str,
        paths: list[PathState],
        chunk_size_bytes: int = 4096,
        scheduler_variant: str = "DB-DSP",
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retransmit_policy: str = RetransmitPolicy.RTX_CL.value,
        in_flight: int = DEFAULT_IN_FLIGHT,
        per_path_in_flight: int = DEFAULT_PER_PATH_IN_FLIGHT,
        seed: int | None = None,
        db_session=None,
        on_event: Optional[Callable[[str, dict], None]] = None,
    ):
        self.run_id = run_id
        self.transfer_id = transfer_id
        self.chunk_size_bytes = chunk_size_bytes
        self.variant = SchedulerVariant(scheduler_variant)
        self.retransmit = RetransmitPolicy(retransmit_policy)
        self.max_retries = max_retries
        self.in_flight = in_flight
        self.per_path_in_flight = per_path_in_flight
        self.db_session = db_session

        #: Optional observer called as ``(event_type, payload)`` as each chunk
        #: moves. Purely a notification channel: nothing here changes which path
        #: is chosen, whether a chunk is retried, or what the result is. An
        #: observer that raises is ignored, because a progress display must never
        #: be able to fail a transfer.
        self.on_event = on_event

        #: The reassembled payload, kept so the receiver's bytes can be handed
        #: back for download. This is the same object the SHA-256 was computed
        #: over — never a second copy rebuilt for display.
        self.reassembled_bytes: bytes | None = None

        self._paths = {p.path_id: p for p in paths}
        self._scheduler = self._build_scheduler(paths)

        if self.retransmit == RetransmitPolicy.RTX_CL:
            self._rtx = RtxClPolicy(self._scheduler)
        else:
            self._rtx = RtxCwndPolicy(self._scheduler)

        self._rng = random.Random(seed) if seed is not None else random.Random()
        self._path_in_flight: dict[str, int] = {pid: 0 for pid in self._paths}
        self._path_locks: dict[str, asyncio.Lock] = {pid: asyncio.Lock() for pid in self._paths}
        self._path_free_at: dict[str, float] = {
            pid: time.monotonic() * 1000.0 for pid in self._paths
        }
        self._arrival_order: list[int] = []
        self._arrival_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Scheduler construction
    # ------------------------------------------------------------------

    def _build_scheduler(self, paths: list[PathState]) -> BaseScheduler:
        """All variants share the same PathState objects so EWMA stays coherent."""
        if self.variant == SchedulerVariant.DB_DSP:
            return DBDSPScheduler(paths, max_consecutive_failures=3)
        if self.variant == SchedulerVariant.ROUND_ROBIN:
            return RoundRobinScheduler(paths)
        if self.variant == SchedulerVariant.WEIGHTED_RR:
            return WeightedRoundRobinScheduler(paths)
        if self.variant == SchedulerVariant.SINGLE_PATH:
            return SinglePathScheduler(paths)
        if self.variant == SchedulerVariant.NAIVE_DELAY:
            return NaiveDelayScheduler(paths)
        raise ValueError(f"unknown scheduler variant {self.variant!r}")

    # ------------------------------------------------------------------
    # Main transfer
    # ------------------------------------------------------------------

    async def transfer(self, data: bytes) -> TransferResult:
        started_at = datetime.utcnow()
        t0 = time.monotonic()

        source_sha256 = _sha256(data)
        chunks = _chunk_data(data, self.chunk_size_bytes)
        total_chunks = len(chunks)

        log.info(
            "[%s] Starting %s transfer: %d bytes → %d chunks (variant=%s, rtx=%s)",
            self.transfer_id, self.variant.label, len(data), total_chunks,
            self.variant.value, self.retransmit.value,
        )

        records = [
            ChunkRecord(
                chunk_index=i, total_chunks=total_chunks,
                data=c, sha256=_sha256(c),
                offset=i * self.chunk_size_bytes,
            )
            for i, c in enumerate(chunks)
        ]
        self._emit("transfer.started", {
            "total_chunks": total_chunks,
            "chunk_size_bytes": self.chunk_size_bytes,
            "file_size_bytes": len(data),
            "scheduler_variant": self.variant.value,
            "retransmit_policy": self.retransmit.value,
            "source_sha256": source_sha256,
            "paths": [
                {"path_id": p.path_id, "fd_estimate_ms": p.fd_estimate_ms,
                 "tp_estimate_bps": p.tp_estimate_bps, "loss_rate": p.loss_rate}
                for p in self._paths.values()
            ],
        })

        delivered: dict[int, bytes] = {}
        retransmission_count = 0
        per_path_utilisation: dict[str, int] = {pid: 0 for pid in self._paths}
        per_path_retrans: dict[str, int] = {pid: 0 for pid in self._paths}
        exhausted: list[int] = []

        semaphore = asyncio.Semaphore(self.in_flight)
        tasks: list[asyncio.Task] = []

        async def worker(record: ChunkRecord) -> None:
            """Deliver one chunk with bounded retries, recording arrival order."""
            nonlocal retransmission_count
            async with semaphore:
                ok = False
                for attempt in range(1, self.max_retries + 2):
                    record.attempts = attempt
                    tried: set[str] = set()

                    if attempt == 1:
                        path_id = self._select_path(exclude=set())
                    else:
                        path_id = self._rtx.select(
                            chunk_size_bytes=self.chunk_size_bytes, exclude=tried
                        )

                    if path_id is None:
                        break

                    record.path_id = path_id
                    record.status = "IN_FLIGHT"
                    self._path_in_flight[path_id] = self._path_in_flight.get(path_id, 0) + 1
                    self._emit("chunk.assigned", {
                        "chunk_index": record.chunk_index,
                        "path_id": path_id,
                        "attempt": attempt,
                        "offset": record.offset,
                        "size_bytes": len(record.data),
                        "sha256": record.sha256,
                    })
                    try:
                        rtt_ms = await self._deliver(record.data, path_id)
                    finally:
                        self._path_in_flight[path_id] -= 1

                    if rtt_ms is not None:
                        record.status = "DELIVERED"
                        delivered[record.chunk_index] = record.data
                        per_path_utilisation[path_id] = per_path_utilisation.get(path_id, 0) + 1
                        self._scheduler.record_ack(path_id, rtt_ms, len(record.data))
                        async with self._arrival_lock:
                            arrival_position = len(self._arrival_order)
                            self._arrival_order.append(record.chunk_index)
                        # Reported with the arrival position so a receiver can
                        # show that a chunk arrived before a lower-numbered one.
                        self._emit("chunk.delivered", {
                            "chunk_index": record.chunk_index,
                            "path_id": path_id,
                            "attempt": attempt,
                            "rtt_ms": round(rtt_ms, 3),
                            "arrival_position": arrival_position,
                            "out_of_order": self._arrival_order[arrival_position] < max(
                                self._arrival_order[:arrival_position], default=-1
                            ),
                        })
                        ok = True
                        break

                    # Simulated loss
                    self._scheduler.record_failure(path_id)
                    retransmission_count += 1
                    per_path_retrans[path_id] = per_path_retrans.get(path_id, 0) + 1
                    tried.add(path_id)
                    self._emit("chunk.lost", {
                        "chunk_index": record.chunk_index,
                        "path_id": path_id,
                        "attempt": attempt,
                        "max_attempts": self.max_retries + 1,
                    })
                    log.debug(
                        "[%s] Chunk %d lost on %s (attempt %d/%d)",
                        self.transfer_id, record.chunk_index, path_id, attempt, self.max_retries + 1,
                    )

                if not ok:
                    record.status = "FAILED"
                    exhausted.append(record.chunk_index)
                    self._emit("chunk.failed", {
                        "chunk_index": record.chunk_index,
                        "attempts": record.attempts,
                    })
                    log.error(
                        "[%s] Chunk %d FAILED after %d attempts.",
                        self.transfer_id, record.chunk_index, record.attempts,
                    )

        # Dispatch everything, bounded by the semaphore.
        for record in records:
            tasks.append(asyncio.create_task(worker(record)))
        await asyncio.gather(*tasks)

        # --- Reassembly and verification ---
        delivered_chunks = len(delivered)
        failed_chunks = total_chunks - delivered_chunks
        out_of_order_count = self._count_out_of_order(self._arrival_order)

        receiver_sha256: str | None = None
        integrity_ok = False

        if delivered_chunks == total_chunks:
            # Reassemble by chunk index, not arrival order. `delivered` is keyed
            # by chunk_index precisely so a chunk that arrived late still lands
            # at its own offset.
            self.reassembled_bytes = b"".join(
                delivered[i] for i in range(total_chunks)
            )
            receiver_sha256 = _sha256(self.reassembled_bytes)
            integrity_ok = receiver_sha256 == source_sha256
            self._emit("transfer.reassembled", {
                "total_chunks": total_chunks,
                "reconstructed_bytes": len(self.reassembled_bytes),
                "arrival_order": list(self._arrival_order),
                "out_of_order_count": out_of_order_count,
            })
            self._emit(
                "transfer.verified" if integrity_ok else "transfer.integrity_failed",
                {
                    "source_sha256": source_sha256,
                    "receiver_sha256": receiver_sha256,
                    "integrity_ok": integrity_ok,
                },
            )
            if integrity_ok:
                log.info("[%s] Transfer complete. SHA-256 match OK", self.transfer_id)
            else:
                log.error(
                    "[%s] SHA-256 MISMATCH src=%s rx=%s",
                    self.transfer_id, source_sha256[:16], (receiver_sha256 or "")[:16],
                )
        else:
            self.reassembled_bytes = None
            self._emit("transfer.incomplete", {
                "delivered_chunks": delivered_chunks,
                "total_chunks": total_chunks,
                "missing_chunks": sorted(set(range(total_chunks)) - set(delivered))[:50],
            })
            log.error(
                "[%s] Bounded failure: %d/%d chunks undelivered (indices: %s)",
                self.transfer_id, failed_chunks, total_chunks,
                sorted(exhausted)[:20],
            )

        duration = time.monotonic() - t0
        result = TransferResult(
            transfer_id=self.transfer_id,
            run_id=self.run_id,
            scheduler_variant=self.variant.value,
            total_chunks=total_chunks,
            delivered_chunks=delivered_chunks,
            failed_chunks=failed_chunks,
            source_sha256=source_sha256,
            receiver_sha256=receiver_sha256,
            integrity_ok=integrity_ok,
            started_at=started_at,
            ended_at=datetime.utcnow(),
            duration_s=duration,
            out_of_order_count=out_of_order_count,
            retransmission_count=retransmission_count,
            per_path_utilisation=per_path_utilisation,
            retransmit_policy=self.retransmit.value,
            goodput_bps=(len(data) * 8) / duration if duration > 0 else 0.0,
            arrival_order=list(self._arrival_order),
            per_path_retransmissions=per_path_retrans,
        )

        await self._persist(result, records)
        return result

    # ------------------------------------------------------------------
    # Out-of-order accounting
    # ------------------------------------------------------------------

    @staticmethod
    def _count_out_of_order(arrival_order: list[int]) -> int:
        """
        Count chunks that arrived after a higher-indexed chunk.

        Walks the receiver's arrival sequence tracking the highest index seen;
        every chunk below that watermark arrived "late" relative to something
        already delivered. This is the reordering-event metric PRD §11 compares
        DB-DSP against round-robin on.
        """
        high_water = -1
        out_of_order = 0
        for idx in arrival_order:
            if idx < high_water:
                out_of_order += 1
            else:
                high_water = idx
        return out_of_order

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _emit(self, event_type: str, payload: dict) -> None:
        """
        Notify the observer, never letting it break the transfer.

        Called from deep inside the chunk workers, concurrently. An observer that
        raises is dropped for this call: a progress display must not be able to
        lose a file.
        """
        if self.on_event is None:
            return
        try:
            self.on_event(event_type, payload)
        except Exception as exc:
            log.debug("[%s] observer raised on %s: %s", self.transfer_id, event_type, exc)

    # ------------------------------------------------------------------
    # Path selection
    # ------------------------------------------------------------------

    def _select_path(self, exclude: set[str] | None = None) -> str | None:
        exclude = set(exclude or set())

        # Single-path variants must not spill onto another path just because
        # their window is full; their whole semantics depend on staying pinned.
        if not getattr(self._scheduler, "honours_in_flight_cap", True):
            return self._scheduler.select_path(
                chunk_size_bytes=self.chunk_size_bytes, exclude=exclude
            )

        # Respect the per-path in-flight cap so one path cannot drain the window.
        saturated = {
            pid for pid, n in self._path_in_flight.items()
            if n >= self.per_path_in_flight
        }
        saturated -= exclude
        if saturated:
            eligible = [p for p in self._scheduler.up_path_ids if p not in saturated]
            if not eligible:
                # Everything is saturated — fall back to ignoring the cap rather
                # than stalling the transfer.
                return self._scheduler.select_path(
                    chunk_size_bytes=self.chunk_size_bytes, exclude=exclude
                )
            # Restrict to unsaturated paths, then let the variant choose among them.
            original = {pid: p.status for pid, p in self._scheduler.paths.items()}
            for pid in saturated:
                self._scheduler.paths[pid].status = PathStatus.DOWN
            try:
                return self._scheduler.select_path(
                    chunk_size_bytes=self.chunk_size_bytes, exclude=exclude
                )
            finally:
                for pid, st in original.items():
                    self._scheduler.paths[pid].status = st

        return self._scheduler.select_path(
            chunk_size_bytes=self.chunk_size_bytes, exclude=exclude
        )

    # ------------------------------------------------------------------
    # Simulated delivery
    # ------------------------------------------------------------------

    async def _deliver(self, data: bytes, path_id: str) -> float | None:
        """
        Simulate one chunk hop over path_id.

        Returns the observed round-trip time in ms on success, or None on
        simulated loss.

        A path is a *serialised* resource: one transmission occupies the link,
        so N paths aggregate N x the bandwidth of one. Without that, every
        concurrent chunk would sleep independently, bandwidth would be free,
        and multipath could never be faster than single-path — the comparison
        would measure nothing.

        Queueing delay is folded into the returned RTT, which is what lets
        EWMA-fed schedulers notice a congested path and spread out. Keep the
        transmission term aligned with PathState.arrival_ms().

        A real deployment replaces this with a TCP write on that path's
        connection; nothing above this function changes.
        """
        path = self._scheduler.paths.get(path_id) or self._paths[path_id]

        # Loss is probabilistic from the path's configured rate.
        if self._rng.random() < path.loss_rate:
            return None

        tx_ms = (len(data) * 8) / max(path.tp_estimate_bps, _TP_MIN_BPS) * 1000.0
        # Jitter models the reordering netem produces under delay.
        jitter = self._rng.gauss(0.0, 1.0)
        hop_ms = max(0.5, tx_ms + path.fd_estimate_ms + jitter)

        async with self._path_locks[path_id]:
            now = time.monotonic() * 1000.0
            start = max(now, self._path_free_at[path_id])
            self._path_free_at[path_id] = start + hop_ms
            queue_wait_ms = start - now

        await asyncio.sleep(queue_wait_ms / 1000.0)

        # The RTT reported to the estimators is the *link* round trip, excluding
        # the simulator's own queueing delay. Folding queueing in here would
        # feed a congestion-inflated RTT into PathState.update_from_ack(), whose
        # throughput estimate is size_bits / rtt — that drives TP toward zero,
        # which inflates transmission time, which creates more queueing. It is a
        # positive feedback loop that never settles.
        return hop_ms * 2.0

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _persist(self, result: TransferResult, records: list[ChunkRecord]) -> None:
        if self.db_session is None:
            return
        try:
            from netrescue.core.models import Chunk, Transfer

            xfer = Transfer(
                transfer_id=result.transfer_id,
                run_id=self.run_id,
                source_node_id="local",
                dest_node_id="local",
                filename=f"transfer_{result.transfer_id[:8]}",
                file_size_bytes=sum(len(r.data) for r in records),
                total_chunks=result.total_chunks,
                scheduler_variant=result.scheduler_variant,
                source_sha256=result.source_sha256,
                receiver_sha256=result.receiver_sha256,
                integrity_ok=result.integrity_ok,
                started_at=result.started_at,
                ended_at=result.ended_at,
                status="COMPLETE" if result.integrity_ok else "FAILED",
            )
            self.db_session.add(xfer)
            for r in records:
                self.db_session.add(Chunk(
                    transfer_id=result.transfer_id,
                    chunk_index=r.chunk_index,
                    total_chunks=r.total_chunks,
                    sha256=r.sha256,
                    size_bytes=len(r.data),
                    path_id=r.path_id,
                    attempts=r.attempts,
                    status=r.status,
                ))
            self.db_session.commit()
            log.info("[%s] Transfer persisted.", result.transfer_id)
        except Exception as exc:
            log.warning("[%s] DB persist failed (degraded mode): %s", result.transfer_id, exc)