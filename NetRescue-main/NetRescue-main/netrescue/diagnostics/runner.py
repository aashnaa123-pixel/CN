"""
netrescue/diagnostics/runner.py — repeatable application-level benchmarks.

A benchmark here is: run the same real probe a configurable number of times and
aggregate the results into a structured, exportable record. It is deliberately
standalone — it imports neither Feature A nor Feature B — so the diagnostics
feature keeps working if either of them is unavailable.

Two kinds of result are produced and never mixed:

  * a **real measurement** (``simulated: false``) from the OS `ping` binary,
    which is what this machine's network actually did just now; and
  * a **controlled simulation** (``simulated: true``) from faults.py, which is
    deterministic for a fixed seed but says nothing about the real network.

A fixed seed can make a simulation repeatable. It cannot make real network
conditions deterministic, and nothing here pretends otherwise.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import random
import socket
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from netrescue.diagnostics.config import SOFTWARE_VERSION, config_hash
from netrescue.diagnostics.probes import ProbeError, ping, validate_target

log = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _environment() -> dict[str, Any]:
    """Host facts recorded with every run so a result can be interpreted later."""
    return {
        "os": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "hostname": socket.gethostname(),
        "python": sys.version.split()[0],
        "software_version": SOFTWARE_VERSION,
    }


# ---------------------------------------------------------------------------
# Specification
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkSpec:
    """Everything that determines a benchmark, so the run can be reproduced."""

    target: str
    count: int = 4             # probes per repetition
    timeout_s: float = 2.0     # per-probe wait
    repetitions: int = 3       # how many times the whole probe set is repeated
    interval_s: float = 0.0    # pause between repetitions
    label: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "kind": "real_ping_benchmark",
            "target": self.target,
            "count_per_repetition": self.count,
            "timeout_s": self.timeout_s,
            "repetitions": self.repetitions,
            "interval_s": self.interval_s,
            "label": self.label,
            "units": {"latency": "ms", "loss": "fraction", "duration": "s"},
            "software_version": SOFTWARE_VERSION,
            # A real network cannot be seeded. Stated explicitly so nobody reads
            # a "reproducible" claim into a real measurement.
            "seed": None,
            "deterministic": False,
        }


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class RepetitionOutcome:
    index: int
    ok: bool
    sent: int = 0
    received: int = 0
    lost: int = 0
    loss_rate: float = 0.0
    samples_ms: list[float] = field(default_factory=list)
    min_ms: float | None = None
    avg_ms: float | None = None
    max_ms: float | None = None
    stddev_ms: float | None = None
    duration_s: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "ok": self.ok,
            "sent": self.sent,
            "received": self.received,
            "lost": self.lost,
            "loss_rate": round(self.loss_rate, 6),
            "min_ms": self.min_ms,
            "avg_ms": self.avg_ms,
            "max_ms": self.max_ms,
            "stddev_ms": self.stddev_ms,
            "samples_ms": [round(s, 3) for s in self.samples_ms],
            "duration_s": round(self.duration_s, 3),
            "error": self.error,
        }


@dataclass
class BenchmarkRun:
    run_id: str
    spec: dict[str, Any]
    config_hash: str
    environment: dict[str, Any]
    started_at: datetime
    simulated: bool = False
    source: str = "real_ping"
    repetitions: list[RepetitionOutcome] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    ended_at: datetime | None = None
    limitations: list[str] = field(default_factory=lambda: [
        "Real network results depend on the current path, local radio/wired "
        "conditions, the peer's policy and ICMP handling; they are not "
        "deterministic and a repeated run may differ.",
        "ICMP round-trip time is not application latency.",
        "The configuration hash identifies the configuration, not the validity "
        "of the experiment.",
    ])

    # -- aggregates across every probe of every repetition ------------------

    @property
    def successful_repetitions(self) -> int:
        return sum(1 for r in self.repetitions if r.ok)

    @property
    def failed_repetitions(self) -> int:
        return len(self.repetitions) - self.successful_repetitions

    @property
    def total_sent(self) -> int:
        return sum(r.sent for r in self.repetitions)

    @property
    def total_received(self) -> int:
        return sum(r.received for r in self.repetitions)

    @property
    def total_lost(self) -> int:
        return max(0, self.total_sent - self.total_received)

    @property
    def packet_loss_rate(self) -> float:
        if self.total_sent == 0:
            return 0.0
        return self.total_lost / self.total_sent

    @property
    def all_samples_ms(self) -> list[float]:
        pooled: list[float] = []
        for rep in self.repetitions:
            pooled.extend(rep.samples_ms)
        return pooled

    def latency(self) -> dict[str, float | None]:
        samples = self.all_samples_ms
        if not samples:
            return {"min_ms": None, "avg_ms": None, "max_ms": None, "stddev_ms": None,
                    "median_ms": None, "samples": 0}
        return {
            "min_ms": round(min(samples), 3),
            "avg_ms": round(statistics.fmean(samples), 3),
            "max_ms": round(max(samples), 3),
            "stddev_ms": round(statistics.pstdev(samples), 3) if len(samples) >= 2 else None,
            "median_ms": round(statistics.median(samples), 3),
            "samples": len(samples),
        }

    @property
    def status(self) -> str:
        if not self.repetitions:
            return "FAILED"
        if self.successful_repetitions == len(self.repetitions):
            return "DONE"
        if self.successful_repetitions > 0:
            return "PARTIAL"
        return "FAILED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "kind": "benchmark",
            "source": self.source,
            "simulated": self.simulated,
            "status": self.status,
            "config": self.spec,
            "config_hash": self.config_hash,
            "software_version": SOFTWARE_VERSION,
            "environment": self.environment,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "repetitions": [r.as_dict() for r in self.repetitions],
            "successful_repetitions": self.successful_repetitions,
            "failed_repetitions": self.failed_repetitions,
            "aggregate": {
                "sent": self.total_sent,
                "received": self.total_received,
                "lost": self.total_lost,
                "packet_loss_rate": round(self.packet_loss_rate, 6),
                "packet_loss_pct": f"{self.packet_loss_rate * 100:.2f}%",
                **self.latency(),
                "units": {"latency": "ms", "loss": "fraction"},
            },
            "errors": self.errors,
            "limitations": self.limitations,
        }

    # -- exports ------------------------------------------------------------

    def to_json(self) -> str:
        import json

        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def to_csv(self) -> str:
        """
        Per-repetition CSV. One row per repetition, plus a final aggregate row.

        Loss is a percentage here because a spreadsheet reads ``50%`` better
        than ``0.5``; the JSON and the API keep the fraction.
        """
        import csv
        import io

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "run_id", "target", "repetition", "ok", "sent", "received", "lost",
            "loss_pct", "min_ms", "avg_ms", "max_ms", "stddev_ms", "duration_s",
            "error",
        ])
        for rep in self.repetitions:
            writer.writerow([
                self.run_id,
                self.spec.get("target", ""),
                rep.index,
                int(rep.ok),
                rep.sent,
                rep.received,
                rep.lost,
                f"{rep.loss_rate * 100:.2f}",
                _fmt(rep.min_ms),
                _fmt(rep.avg_ms),
                _fmt(rep.max_ms),
                _fmt(rep.stddev_ms),
                f"{rep.duration_s:.3f}",
                rep.error or "",
            ])
        agg = self.latency()
        writer.writerow([
            self.run_id,
            self.spec.get("target", ""),
            "AGGREGATE",
            int(self.status == "DONE"),
            self.total_sent,
            self.total_received,
            self.total_lost,
            f"{self.packet_loss_rate * 100:.2f}",
            _fmt(agg["min_ms"]),
            _fmt(agg["avg_ms"]),
            _fmt(agg["max_ms"]),
            _fmt(agg["stddev_ms"]),
            "",
            "; ".join(self.errors),
        ])
        return buf.getvalue()


def _fmt(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class BenchmarkEngine:
    """
    Executes a real ping benchmark, one repetition at a time.

    Each `ping` call is a blocking subprocess, so it is dispatched to a worker
    thread (``asyncio.to_thread``) and this coroutine never blocks the event
    loop.
    """

    def __init__(self, spec: BenchmarkSpec):
        self.spec = spec

    def _snapshot(self) -> dict[str, Any]:
        return self.spec.snapshot()

    async def run(self) -> BenchmarkRun:
        spec = self.spec
        validate_target(spec.target)  # fail fast on a bad target

        snapshot = self._snapshot()
        run = BenchmarkRun(
            run_id=f"diag-{uuid.uuid4().hex[:10]}",
            spec=snapshot,
            config_hash=config_hash(snapshot),
            environment=_environment(),
            started_at=utc_now(),
            simulated=False,
            source="real_ping",
        )

        for index in range(max(1, int(spec.repetitions))):
            outcome = await self._one_repetition(index)
            run.repetitions.append(outcome)
            if outcome.error:
                run.errors.append(f"repetition {index}: {outcome.error}")
            if spec.interval_s > 0 and index < spec.repetitions - 1:
                await asyncio.sleep(spec.interval_s)

        run.ended_at = utc_now()
        log.info(
            "[diag %s] benchmark %s -> %s, loss=%.1f%%",
            run.run_id, spec.target, run.status, run.packet_loss_rate * 100,
        )
        return run

    async def _one_repetition(self, index: int) -> RepetitionOutcome:
        spec = self.spec
        started = time.perf_counter()
        try:
            response = await asyncio.to_thread(
                ping, spec.target, count=spec.count, timeout_s=spec.timeout_s,
            )
        except ProbeError as exc:
            return RepetitionOutcome(
                index=index, ok=False, error=f"{exc}",
                duration_s=time.perf_counter() - started,
            )

        return RepetitionOutcome(
            index=index,
            # A repetition "succeeded" when the probe actually got replies. A
            # run of pure loss is a real result, but it is not a success.
            ok=response.received > 0,
            sent=response.sent,
            received=response.received,
            lost=max(0, response.sent - response.received),
            loss_rate=response.loss_rate,
            samples_ms=list(response.samples_ms),
            min_ms=response.min_ms,
            avg_ms=response.avg_ms,
            max_ms=response.max_ms,
            stddev_ms=response.stddev_ms,
            duration_s=response.duration_s,
            error=None if response.received > 0 else "no reply from target",
        )


async def run_benchmark(spec: BenchmarkSpec) -> BenchmarkRun:
    """Convenience entry point used by the API and the CLI."""
    return await BenchmarkEngine(spec).run()


# ---------------------------------------------------------------------------
# Simulated benchmark (used by the deterministic fault scenarios)
# ---------------------------------------------------------------------------


def simulated_benchmark(
    spec: BenchmarkSpec,
    *,
    seed: int,
    latency_ms: float,
    jitter_ms: float,
    loss_rate: float,
    unreachable: bool = False,
) -> BenchmarkRun:
    """
    Build a BenchmarkRun from a seeded simulation.

    Deterministic for a fixed ``seed`` and configuration: the same inputs
    always produce the same samples. The result is marked ``simulated`` and
    carries the caveat that it describes a model, not this machine's network.
    """
    rng = random.Random(seed)
    snapshot = {
        **spec.snapshot(),
        "kind": "simulated_fault_scenario",
        "seed": seed,
        "latency_ms": latency_ms,
        "jitter_ms": jitter_ms,
        "loss_rate": loss_rate,
        "unreachable": unreachable,
        "deterministic": True,
    }
    run = BenchmarkRun(
        run_id=f"sim-{uuid.uuid4().hex[:10]}",
        spec=snapshot,
        config_hash=config_hash(snapshot),
        environment={**_environment(), "simulated": True},
        started_at=utc_now(),
        simulated=True,
        source="simulated_fault",
    )

    lim = latency_ms
    base = float(spec.timeout_s) * 1000.0
    for index in range(max(1, int(spec.repetitions))):
        samples: list[float] = []
        sent = int(spec.count)
        received = 0
        for _ in range(sent):
            if unreachable or rng.random() < loss_rate:
                continue
            sample = max(lim, rng.gauss(lim, max(jitter_ms, 0.0)))
            # A probe that would exceed the timeout is counted as lost, which is
            # exactly how a real timeout behaves.
            if sample > base:
                continue
            samples.append(round(sample, 3))
            received += 1
        lost = sent - received
        run.repetitions.append(RepetitionOutcome(
            index=index,
            ok=received > 0,
            sent=sent,
            received=received,
            lost=lost,
            loss_rate=(lost / sent) if sent else 0.0,
            samples_ms=samples,
            min_ms=round(min(samples), 3) if samples else None,
            avg_ms=round(statistics.fmean(samples), 3) if samples else None,
            max_ms=round(max(samples), 3) if samples else None,
            stddev_ms=round(statistics.pstdev(samples), 3) if len(samples) >= 2 else None,
            error=None if received > 0 else "no reply from target (simulated)",
        ))

    run.limitations = [
        "SIMULATED: these numbers come from a seeded model, not from a real "
        "network probe.",
        "Deterministic for a fixed seed and configuration; real network "
        "conditions cannot be made deterministic by a seed.",
        "The configuration hash identifies the configuration, not the validity "
        "of the experiment.",
    ]
    run.ended_at = utc_now()
    return run
