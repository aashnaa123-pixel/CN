"""
netrescue/benchmark/runner.py

Benchmark workload generator and scenario runner for Feature D (PRD §6.2).

Measures: RTT/latency, throughput, message/datagram loss, jitter, run duration.
Stores raw results in PostgreSQL via batch writes (≥1 Hz; never per-message).
Exports CSV; reports median and p95.

Usage (standalone — no recovery controller required)::

    from netrescue.benchmark.runner import BenchmarkConfig, BenchmarkRunner
    cfg = BenchmarkConfig(run_id="test-1", sender_host="127.0.0.1",
                          sender_port=9100, target_host="127.0.0.1",
                          target_port=9101, message_count=200,
                          message_size_bytes=256)
    runner = BenchmarkRunner(cfg)
    results = asyncio.run(runner.run())
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Environment capture (PRD §6.2, §6.8 — record kernel version per run)
# ---------------------------------------------------------------------------


def _collect_environment() -> dict[str, Any]:
    """
    Capture host facts needed to interpret a benchmark result.

    PRD §6.8 warns that `tc`/`netem` behaviour varies by kernel version, so the
    kernel string is recorded with every run rather than assumed constant.
    """
    import platform
    import sys

    kernel = ""
    try:
        kernel = platform.release()
    except Exception:  # pragma: no cover - platform quirks
        kernel = "unknown"
    return {
        "os": platform.system(),
        "kernel": kernel,
        "python": sys.version.split()[0],
        "machine": platform.machine(),
        "emulated": True,  # all conditions are synthetic — never label as real
    }


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkConfig:
    """Scenario configuration for a single benchmark run."""

    run_id: str
    sender_host: str
    sender_port: int
    target_host: str
    target_port: int
    message_count: int = 100
    message_size_bytes: int = 256
    send_interval_s: float = 0.01       # Delay between sends (0 = flood)
    protocol: str = "UDP"               # "UDP" or "TCP"
    # DB write batching (PRD §2.2 rule: ≥1 Hz, never per-message)
    db_batch_interval_s: float = 1.0
    label: str = ""                     # Human-readable condition label


# ---------------------------------------------------------------------------
# Per-message record
# ---------------------------------------------------------------------------


@dataclass
class MessageRecord:
    seq: int
    sent_at: float        # time.monotonic()
    received_at: float | None = None
    lost: bool = False

    @property
    def rtt_ms(self) -> float | None:
        if self.received_at is None:
            return None
        return (self.received_at - self.sent_at) * 1000.0


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkResults:
    run_id: str
    label: str
    protocol: str
    message_count: int
    message_size_bytes: int
    received_count: int = 0
    lost_count: int = 0
    run_duration_s: float = 0.0
    rtt_samples_ms: list[float] = field(default_factory=list)
    #: Host/kernel facts recorded per run (PRD §6.2 — store environment).
    environment: dict[str, Any] = field(default_factory=dict)
    #: Datagrams the echo endpoint actually saw, for cross-checking against the
    #: sender's own lost_count (PRD §6.6 measurement-point separation).
    echo_arrivals: int = 0

    # --- Computed stats ---

    @property
    def loss_rate(self) -> float:
        if self.message_count == 0:
            return 0.0
        return self.lost_count / self.message_count

    @property
    def throughput_bps(self) -> float:
        if self.run_duration_s == 0:
            return 0.0
        return (self.received_count * self.message_size_bytes * 8) / self.run_duration_s

    @property
    def rtt_median_ms(self) -> float | None:
        if not self.rtt_samples_ms:
            return None
        return statistics.median(self.rtt_samples_ms)

    @property
    def rtt_p95_ms(self) -> float | None:
        if not self.rtt_samples_ms:
            return None
        sorted_rtts = sorted(self.rtt_samples_ms)
        idx = int(len(sorted_rtts) * 0.95)
        return sorted_rtts[min(idx, len(sorted_rtts) - 1)]

    @property
    def jitter_ms(self) -> float | None:
        """Mean deviation between consecutive RTT samples (RFC 3393 style)."""
        if len(self.rtt_samples_ms) < 2:
            return None
        return statistics.mean(
            abs(b - a) for a, b in zip(self.rtt_samples_ms, self.rtt_samples_ms[1:])
        )

    def summary(self) -> dict:
        return {
            "run_id": self.run_id,
            "label": self.label,
            "protocol": self.protocol,
            "message_count": self.message_count,
            "message_size_bytes": self.message_size_bytes,
            "received_count": self.received_count,
            "lost_count": self.lost_count,
            # A fraction, not a preformatted string, so callers can do
            # arithmetic on it. This matches PathSpec.loss_rate everywhere else.
            # loss_rate_pct is the display form.
            "loss_rate": round(self.loss_rate, 6),
            "loss_rate_pct": f"{self.loss_rate * 100:.2f}%",
            "run_duration_s": round(self.run_duration_s, 3),
            "throughput_bps": round(self.throughput_bps, 1),
            "rtt_median_ms": self.rtt_median_ms,
            "rtt_p95_ms": self.rtt_p95_ms,
            "jitter_ms": self.jitter_ms,
            "rtt_samples": len(self.rtt_samples_ms),
            "echo_arrivals": self.echo_arrivals,
            "environment": self.environment,
            # Measurement semantics, restated with every report (PRD §6.6).
            "measurement_semantics": {
                "loss": "application-message loss, measured at the socket layer",
                "rtt": "sender send-time to echoed-reply receive-time",
                "note": "Conditions are emulated; not real-world measurements.",
            },
        }

    def to_csv(self) -> str:
        """Export all RTT samples as CSV string."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["run_id", "label", "protocol", "seq", "rtt_ms"])
        for i, rtt in enumerate(self.rtt_samples_ms):
            writer.writerow([self.run_id, self.label, self.protocol, i, f"{rtt:.4f}"])
        return buf.getvalue()


# ---------------------------------------------------------------------------
# UDP Echo Server (receiver side)
# ---------------------------------------------------------------------------


class UDPEchoProtocol(asyncio.DatagramProtocol):
    """
    Echo server for the UDP benchmark.

    Each datagram is timestamped on arrival and echoed back with a 4-byte
    sequence header so the sender can match replies to sends. Recording arrival
    timestamps here gives the *application-layer* delivery measurement point
    that PRD §6.6 requires, distinct from kernel-level `tc` statistics.
    """

    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self.received: dict[int, float] = {}   # seq -> monotonic arrival time

    def connection_made(self, transport: asyncio.BaseTransport) -> None:  # type: ignore[override]
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        now = time.monotonic()
        if len(data) >= 4:
            seq = int.from_bytes(data[:4], "big")
            self.received[seq] = now
        if self.transport:
            self.transport.sendto(data, addr)

    def error_received(self, exc: Exception) -> None:
        log.debug("[echo] transport error: %s", exc)


class BenchmarkSenderProtocol(asyncio.DatagramProtocol):
    """Correlates echoed replies back to their send timestamps."""

    def __init__(self, records: dict[int, MessageRecord], on_reply) -> None:
        self._records = records
        self._on_reply = on_reply

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) < 4:
            return
        seq = int.from_bytes(data[:4], "big")
        record = self._records.get(seq)
        if record is not None and record.received_at is None:
            record.received_at = time.monotonic()
            self._on_reply(record)

    def error_received(self, exc: Exception) -> None:
        log.debug("[bench-sender] transport error: %s", exc)


# ---------------------------------------------------------------------------
# Benchmark Runner
# ---------------------------------------------------------------------------


class BenchmarkRunner:
    """
    Runs a configurable latency/throughput benchmark over UDP or TCP.

    PRD §6.2 requirements:
    - Fixed topology, no recovery controller dependency.
    - Measures RTT, throughput, message loss, jitter, run duration.
    - PostgreSQL write: batch at ≥ 1 Hz, never per-message.
    - CSV export; median + p95 reporting.
    """

    def __init__(self, cfg: BenchmarkConfig):
        self.cfg = cfg
        self._records: dict[int, MessageRecord] = {}
        self._db_batch: list[dict] = []
        self.environment = _collect_environment()

    # ------------------------------------------------------------------

    async def _start_echo_server(self) -> tuple[asyncio.DatagramTransport, UDPEchoProtocol]:
        loop = asyncio.get_running_loop()
        protocol = UDPEchoProtocol()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: protocol, local_addr=(self.cfg.target_host, self.cfg.target_port)
        )
        return transport, protocol

    async def _run_udp(self) -> BenchmarkResults:
        """
        UDP round-trip benchmark.

        Sends N datagrams, correlates each echoed reply by its sequence header,
        and derives RTT from the send/receive timestamps. Messages that never
        come back before the deadline are counted as application-message loss —
        note this is measured at the application layer, so it includes kernel
        loss *and* any socket-buffer drops (PRD §6.6).
        """
        results = BenchmarkResults(
            run_id=self.cfg.run_id,
            label=self.cfg.label,
            protocol="UDP",
            message_count=self.cfg.message_count,
            message_size_bytes=self.cfg.message_size_bytes,
            environment=self.environment,
        )

        loop = asyncio.get_running_loop()
        echo_transport, echo_proto = await self._start_echo_server()
        await asyncio.sleep(0.05)  # let the echo socket bind

        replied = 0

        def _on_reply(_record: MessageRecord) -> None:
            nonlocal replied
            replied += 1

        sender_transport, _ = await loop.create_datagram_endpoint(
            lambda: BenchmarkSenderProtocol(self._records, _on_reply),
            remote_addr=(self.cfg.target_host, self.cfg.target_port),
        )

        payload = b"N" * max(self.cfg.message_size_bytes - 4, 1)
        run_start = time.monotonic()
        db_flush_task = asyncio.create_task(self._db_flush_loop())

        try:
            for seq in range(self.cfg.message_count):
                record = MessageRecord(seq=seq, sent_at=time.monotonic())
                self._records[seq] = record
                sender_transport.sendto(seq.to_bytes(4, "big") + payload)
                if self.cfg.send_interval_s > 0:
                    await asyncio.sleep(self.cfg.send_interval_s)

            # Wait until every reply has landed or the deadline expires.
            deadline = time.monotonic() + self._reply_timeout_s()
            while replied < self.cfg.message_count and time.monotonic() < deadline:
                await asyncio.sleep(0.005)

            results.run_duration_s = time.monotonic() - run_start
            records = list(self._records.values())
            results.received_count = sum(1 for r in records if r.received_at is not None)
            results.lost_count = self.cfg.message_count - results.received_count
            results.rtt_samples_ms = [
                r.rtt_ms for r in records if r.rtt_ms is not None
            ]
            results.echo_arrivals = len(echo_proto.received)
        finally:
            db_flush_task.cancel()
            echo_transport.close()
            sender_transport.close()

        self._queue_metric_batch(results)
        await self._flush_db()
        return results

    def _reply_timeout_s(self) -> float:
        """
        Deadline for collecting replies.

        Allow a generous multiple of the transmit window so a slow-but-not-lost
        datagram is not miscounted as loss.
        """
        transmit_window = self.cfg.message_count * max(self.cfg.send_interval_s, 0.001)
        return max(2.0, transmit_window * 2.0 + 1.0)

    async def _run_tcp(self) -> BenchmarkResults:
        """
        TCP throughput/latency benchmark.

        Wire format is a length-prefixed frame so the stream stays framed::

            [2 bytes big-endian payload length][4 bytes seq][payload]

        TCP's own retransmissions hide kernel-level loss, so `lost_count` here
        means *application-message* loss only — the distinction PRD §6.6 insists
        on. Both peer reads carry a timeout so a framing bug surfaces as a
        failure instead of hanging the run forever.
        """
        results = BenchmarkResults(
            run_id=self.cfg.run_id,
            label=self.cfg.label,
            protocol="TCP",
            message_count=self.cfg.message_count,
            message_size_bytes=self.cfg.message_size_bytes,
            environment=self.environment,
        )

        body = max(self.cfg.message_size_bytes - 6, 1)
        read_timeout = 10.0

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while True:
                    header = await asyncio.wait_for(reader.readexactly(2), read_timeout)
                    length = int.from_bytes(header, "big")
                    frame = await asyncio.wait_for(reader.readexactly(4 + length), read_timeout)
                    seq = int.from_bytes(frame[:4], "big")
                    received.append(MessageRecord(
                        seq=seq, sent_at=0.0, received_at=time.monotonic()
                    ))
                    writer.write(b"OK")
                    await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionResetError,
                    asyncio.TimeoutError, BrokenPipeError):
                pass
            finally:
                writer.close()

        received: list[MessageRecord] = []
        server = await asyncio.start_server(handle, self.cfg.target_host, self.cfg.target_port)
        async with server:
            reader, writer = await asyncio.open_connection(self.cfg.target_host, self.cfg.target_port)
            payload = b"N" * body
            run_start = time.monotonic()
            db_flush_task = asyncio.create_task(self._db_flush_loop())
            try:
                for seq in range(self.cfg.message_count):
                    record = MessageRecord(seq=seq, sent_at=time.monotonic())
                    self._records[seq] = record
                    writer.write(len(payload).to_bytes(2, "big") + seq.to_bytes(4, "big") + payload)
                    await writer.drain()
                    await asyncio.wait_for(reader.readexactly(2), read_timeout)
                    record.received_at = time.monotonic()

                results.run_duration_s = time.monotonic() - run_start
                results.received_count = len(received)
                results.lost_count = self.cfg.message_count - results.received_count
                results.rtt_samples_ms = [
                    r.rtt_ms for r in self._records.values() if r.rtt_ms is not None
                ]
            finally:
                db_flush_task.cancel()
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        self._queue_metric_batch(results)
        await self._flush_db()
        return results

    async def _db_flush_loop(self) -> None:
        """Batch flush accumulated metrics to DB at ≥1 Hz (PRD §2.2)."""
        while True:
            await asyncio.sleep(self.cfg.db_batch_interval_s)
            if self._db_batch:
                await self._flush_db()

    async def _flush_db(self) -> None:
        """
        Write pending metric samples to PostgreSQL.

        Best-effort by design: a database outage must degrade to "results still
        measured, not persisted" rather than failing the run or blocking on a
        connect attempt (PRD §2.2 rule 3). The core circuit breaker makes a
        known-down database fail instantly instead of costing a timeout per
        flush.
        """
        batch = self._db_batch[:]
        if not batch:
            return
        self._db_batch.clear()

        try:
            from netrescue.core import db as core_db
            from netrescue.core.models import Experiment, MetricSample

            if core_db.is_degraded():
                log.debug("[benchmark %s] DB degraded — dropping %d samples",
                          self.cfg.run_id, len(batch))
                return

            with core_db.get_session() as session:
                # `metric_samples.run_id` is a foreign key onto `experiments`.
                # A run started from the CLI or from a test has no experiment row
                # yet, so without this every batch write fails the constraint and
                # the measurements are silently never retained.
                if not session.query(Experiment).filter(
                    Experiment.id == self.cfg.run_id
                ).first():
                    now = datetime.now(timezone.utc).replace(tzinfo=None)
                    session.add(Experiment(
                        id=self.cfg.run_id,
                        feature="D",
                        status="RUNNING",
                        started_at=now,
                        config={"protocol": self.cfg.protocol,
                                "message_count": self.cfg.message_count,
                                "message_size_bytes": self.cfg.message_size_bytes,
                                "origin": "benchmark-runner"},
                    ))
                    session.commit()
                for item in batch:
                    session.add(MetricSample(**item))
            core_db.mark_available()
        except Exception as exc:
            from netrescue.core import db as core_db
            core_db.mark_unavailable(str(exc).splitlines()[0])
            log.warning(
                "[benchmark %s] DB batch write failed — continuing in degraded mode: %s",
                self.cfg.run_id, exc,
            )

    def _queue_metric_batch(self, results: BenchmarkResults) -> None:
        """
        Queue summary metrics for the next batch write.

        Only aggregates are queued — never one row per message (PRD §7
        retention rule).
        """
        labels = {"protocol": results.protocol, "label": results.label}
        for name, value, unit in (
            ("rtt_median_ms", float(results.rtt_median_ms or 0.0), "ms"),
            ("rtt_p95_ms", float(results.rtt_p95_ms or 0.0), "ms"),
            ("loss_rate", results.loss_rate, "fraction"),
            ("throughput", results.throughput_bps, "bps"),
        ):
            self._db_batch.append({
                "run_id": results.run_id,
                "name": name,
                "value": value,
                "unit": unit,
                "labels": labels,
            })

    async def run(self) -> BenchmarkResults:
        """Execute the benchmark and return results."""
        log.info(
            "[Benchmark %s] Starting %s run: %d msgs × %d bytes",
            self.cfg.run_id, self.cfg.protocol, self.cfg.message_count,
            self.cfg.message_size_bytes,
        )
        protocol = self.cfg.protocol.upper()
        if protocol == "UDP":
            results = await self._run_udp()
        elif protocol == "TCP":
            results = await self._run_tcp()
        else:
            raise ValueError(f"unsupported protocol {self.cfg.protocol!r}; use UDP or TCP")

        log.info("[Benchmark %s] Complete: %s", self.cfg.run_id, results.summary())
        return results
