"""
netrescue/benchmark/comparison.py

Scheduler and retransmission comparison harness (PRD §6.5, §5.4, §10).

Runs the *same* payload over the *same* heterogeneous path configuration with
each scheduler variant, so the comparison isolates the scheduling policy.

Fairness rules enforced here
-----------------------------
1. Every repetition rebuilds PathState from the declarative PathSpec, so EWMA
   estimates learned by one variant never leak into the next.
2. All variants see an identical payload and identical per-path conditions.
3. At least MIN_REPETITIONS runs per cell; median and p95 are reported, never a
   single sample.
4. Integrity is verified per run; a variant that corrupts data is reported as
   such rather than silently averaged in.

Usage::

    from netrescue.benchmark.comparison import (
        ComparisonConfig, PathSpec, SchedulerComparisonHarness,
    )

    cfg = ComparisonConfig(
        run_id="cmp-1",
        payload_bytes=512_000,
        chunk_size_bytes=4096,
        paths=[
            PathSpec("p1", fd_ms=10.0, tp_bps=8_000_000),
            PathSpec("p2", fd_ms=50.0, tp_bps=4_000_000),
            PathSpec("p3", fd_ms=100.0, tp_bps=2_000_000),
        ],
        repetitions=5,
    )
    report = await SchedulerComparisonHarness(cfg).run()
    print(report.table())
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import random
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from netrescue.benchmark.report import (
    MIN_REPETITIONS,
    ComparisonReport,
    ComparisonRow,
    summarise,
)
from netrescue.transfer.scheduler import PathState
from netrescue.transfer.session import MultipathTransferSession, TransferResult

log = logging.getLogger(__name__)

#: Every variant in PRD §5.3. Comparison is incomplete without the baselines.
ALL_VARIANTS = ["DB-DSP", "RR", "WRR", "SINGLE", "NAIVE_DELAY"]

#: PRD §5.4 requires comparing RTX-CL against the RTX-CWND baseline.
ALL_RETRANSMIT_POLICIES = ["RTX-CL", "RTX-CWND"]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PathSpec:
    """
    Declarative path conditions.

    Frozen so a cell cannot be mutated between variants — that would defeat the
    point of the comparison.
    """

    path_id: str
    fd_ms: float = 10.0
    tp_bps: float = 1_000_000.0
    loss_rate: float = 0.0
    cwnd_bytes: float = 65536.0

    def to_path_state(self) -> PathState:
        return PathState(
            path_id=self.path_id,
            fd_estimate_ms=self.fd_ms,
            tp_estimate_bps=self.tp_bps,
            loss_rate=self.loss_rate,
            cwnd_estimate_bytes=self.cwnd_bytes,
        )


@dataclass
class ComparisonConfig:
    run_id: str
    paths: list[PathSpec]
    payload_bytes: int = 256_000
    chunk_size_bytes: int = 4096
    repetitions: int = MIN_REPETITIONS
    variants: list[str] = field(default_factory=lambda: list(ALL_VARIANTS))
    retransmit_policies: list[str] = field(default_factory=lambda: list(ALL_RETRANSMIT_POLICIES))
    #: With a single RTX policy this becomes the pure scheduler comparison.
    #: With both, you also get the RTX-CL vs RTX-CWND contrast PRD §5.4 wants.
    seed: int | None = 1337

    def workload(self) -> dict[str, Any]:
        return {
            "payload_bytes": self.payload_bytes,
            "chunk_size_bytes": self.chunk_size_bytes,
            "chunks": (self.payload_bytes + self.chunk_size_bytes - 1) // self.chunk_size_bytes,
            "repetitions": self.repetitions,
            "paths": [
                {
                    "path_id": p.path_id,
                    "fd_ms": p.fd_ms,
                    "tp_bps": p.tp_bps,
                    "loss_rate": p.loss_rate,
                }
                for p in self.paths
            ],
            "delay_heterogeneity": _heterogeneity(self.paths),
        }


def _heterogeneity(paths: list[PathSpec]) -> str:
    """Max:min delay ratio — PRD §11 phrases the target as '5:1 heterogeneity'."""
    if not paths:
        return "n/a"
    lo = min(p.fd_ms for p in paths)
    hi = max(p.fd_ms for p in paths)
    return f"{hi / lo:.1f}:1" if lo > 0 else "n/a"


def _environment() -> dict[str, Any]:
    return {
        "os": platform.system(),
        "kernel": platform.release(),
        "python": sys.version.split()[0],
        "emulated": True,
    }


def _payload(cfg: ComparisonConfig, seed: int) -> bytes:
    """
    Deterministic payload so every variant transfers byte-identical data.

    Built by repeating a seeded 4 KiB block. Written with explicit temporaries
    rather than one chained expression: in `a * (b + 1)[: n]` the subscript
    binds to the parenthesised integer, not to the product.
    """
    rng = random.Random(seed)
    block = bytes(rng.getrandbits(8) for _ in range(min(cfg.payload_bytes, 4096)))
    if not block:
        return b""
    repeats = cfg.payload_bytes // len(block) + 1
    return (block * repeats)[: cfg.payload_bytes]


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class SchedulerComparisonHarness:
    """
    Executes the cross-product of scheduler variants × retransmission policies.

    Each cell is repeated ``cfg.repetitions`` times with fresh PathState, and the
    median / p95 of every metric is reported.
    """

    def __init__(self, cfg: ComparisonConfig):
        self.cfg = cfg

    # ------------------------------------------------------------------

    async def run(self) -> ComparisonReport:
        report = ComparisonReport(
            run_id=self.cfg.run_id,
            kind="scheduler",
            generated_at=datetime.utcnow(),
            workload=self.cfg.workload(),
            environment=_environment(),
        )

        payload = _payload(self.cfg, self.cfg.seed or 0)
        base_seed = self.cfg.seed if self.cfg.seed is not None else 0

        for variant in self.cfg.variants:
            for policy in self.cfg.retransmit_policies:
                row = await self._run_cell(variant, policy, payload, base_seed)
                report.rows.append(row)
                log.info("[cmp %s] %s/%s -> %s", self.cfg.run_id, variant, policy, row.as_dict())

        report.findings = self._derive_findings(report)
        return report

    # ------------------------------------------------------------------

    async def _run_cell(
        self, variant: str, policy: str, payload: bytes, base_seed: int
    ) -> ComparisonRow:
        results: list[TransferResult] = []

        for rep in range(self.cfg.repetitions):
            # Fresh PathState per repetition — no EWMA leakage between cells.
            paths = [spec.to_path_state() for spec in self.cfg.paths]
            session = MultipathTransferSession(
                run_id=self.cfg.run_id,
                transfer_id=f"{self.cfg.run_id}-{variant}-{policy}-{rep}",
                paths=paths,
                chunk_size_bytes=self.cfg.chunk_size_bytes,
                scheduler_variant=variant,
                retransmit_policy=policy,
                # Vary the seed per repetition so jitter is not identical each time,
                # while the whole sweep stays reproducible from base_seed.
                seed=base_seed + rep,
            )
            results.append(await session.transfer(payload))

        return self._aggregate(variant, policy, results)

    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate(variant: str, policy: str, results: list[TransferResult]) -> ComparisonRow:
        durations = [r.duration_s for r in results]
        goodputs = [r.goodput_bps for r in results]
        ooo = [float(r.out_of_order_count) for r in results]
        retx = [float(r.retransmission_count) for r in results]
        failed = [float(r.failed_chunks) for r in results]

        # Per-path utilisation is pooled across repetitions.
        pooled: dict[str, int] = {}
        for r in results:
            for pid, n in r.per_path_utilisation.items():
                pooled[pid] = pooled.get(pid, 0) + n

        return ComparisonRow(
            variant=variant,
            retransmit_policy=policy,
            repetitions=len(results),
            duration_s=summarise(durations),
            goodput_bps=summarise(goodputs),
            out_of_order_count=summarise(ooo),
            retransmission_count=summarise(retx),
            failed_chunks_median=summarise(failed)["median"],
            integrity_ok_count=sum(1 for r in results if r.integrity_ok),
            per_path_utilisation=pooled,
        )

    # ------------------------------------------------------------------

    def _derive_findings(self, report: ComparisonReport) -> list[str]:
        """
        Check the observed numbers against the PRD §11 targets.

        Targets are stated as rules, so each finding states whether it passed,
        failed, or could not be evaluated (e.g. zero-loss runs produce no
        retransmissions to compare).
        """
        findings: list[str] = []
        by_key = {(r.variant, r.retransmit_policy): r for r in report.rows}

        # --- DB-DSP out-of-order vs round-robin (PRD §11) ---
        for policy in self.cfg.retransmit_policies:
            dsp = by_key.get(("DB-DSP", policy))
            rr = by_key.get(("RR", policy))
            if not dsp or not rr:
                continue
            dsp_ooo = dsp.out_of_order_count.get("median")
            rr_ooo = rr.out_of_order_count.get("median")
            if dsp_ooo is None or rr_ooo is None:
                continue
            if rr_ooo == 0:
                findings.append(
                    f"[{policy}] DB-DSP vs round-robin out-of-order comparison not "
                    f"evaluable: round-robin reported 0 reordering at "
                    f"{report.workload['delay_heterogeneity']} heterogeneity "
                    f"(DB-DSP={dsp_ooo:.0f}). Increase the delay spread or the "
                    f"chunk size so paths diverge."
                )
            elif dsp_ooo <= 0.5 * rr_ooo:
                findings.append(
                    f"[{policy}] PASS — DB-DSP out-of-order {dsp_ooo:.0f} <= 50% of "
                    f"round-robin {rr_ooo:.0f} (PRD §11 target met)."
                )
            else:
                findings.append(
                    f"[{policy}] Target not met — DB-DSP out-of-order {dsp_ooo:.0f} "
                    f"vs round-robin {rr_ooo:.0f}."
                )

        # --- RTX-CL vs RTX-CWND (PRD §5.4 / §11) ---
        for variant in self.cfg.variants:
            cl = by_key.get((variant, "RTX-CL"))
            cwnd = by_key.get((variant, "RTX-CWND"))
            if not cl or not cwnd:
                continue
            cl_d = cl.duration_s.get("median")
            cw_d = cwnd.duration_s.get("median")
            if not cl_d or not cw_d:
                continue
            if cw_d == 0:
                findings.append(f"[{variant}] RTX comparison not evaluable: zero duration.")
                continue
            ratio = cl_d / cw_d
            if ratio <= 0.9:
                findings.append(
                    f"[{variant}] PASS — RTX-CL transfer time is {ratio * 100:.1f}% of "
                    f"RTX-CWND (<= 90% target, PRD §11)."
                )
            else:
                findings.append(
                    f"[{variant}] RTX-CL is {ratio * 100:.1f}% of RTX-CWND "
                    f"(target <= 90%). No retransmissions may have occurred under "
                    f"these loss settings."
                )

        # --- Integrity ---
        broken = [r for r in report.rows if r.integrity_ok_count < r.repetitions]
        if broken:
            findings.append(
                "WARNING — integrity failures in: "
                + ", ".join(f"{r.variant}/{r.retransmit_policy}" for r in broken)
            )
        else:
            findings.append("All runs verified by SHA-256 (source == receiver).")

        return findings


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_scheduler_comparison(cfg: ComparisonConfig) -> ComparisonReport:
    """Convenience wrapper used by the API layer (PRD §8.1)."""
    return await SchedulerComparisonHarness(cfg).run()


def default_run_id(prefix: str = "cmp") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"