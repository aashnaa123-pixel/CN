"""
netrescue/emulation/harness.py

Emulation orchestration and fault injection (PRD §6.3, §6.4, §10).

Wraps the raw tc_netem calls in a lifecycle that guarantees:

  * the qdisc is torn down even if the run raises (context manager),
  * every applied configuration is verified and hashed before the run starts,
  * the applied configuration is written to the TcConfig audit trail,
  * fault injection is explicit and always reversible.

Emulation validation (PRD §10 "Emulation validation", §11 "measured delay/loss
within 10% of configured values") compares the configured parameters against
what the kernel actually reports, and states which of the two numbers is being
compared so a reader cannot mistake configuration for measurement.
"""

from __future__ import annotations

import logging
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, Sequence

from netrescue.emulation.platform import EmulationUnavailable, emulation_support, require_emulation
from netrescue.emulation.tc_netem import (
    TcNetemConfig,
    apply_config,
    remove_config,
    verify_config,
)

log = logging.getLogger(__name__)

#: PRD §11 — measured emulation error must stay within this fraction.
DEFAULT_TOLERANCE = 0.10


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class EmulationRecord:
    """Audit row for one applied configuration (PRD §7 TcConfig)."""

    run_id: str
    link_id: str
    interface: str
    qdisc_params: dict[str, Any]
    config_hash: str
    applied_at: datetime
    verified: bool
    verification: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "link_id": self.link_id,
            "interface": self.interface,
            "qdisc_params": self.qdisc_params,
            "config_hash": self.config_hash,
            "applied_at": self.applied_at.isoformat(),
            "verified": self.verified,
            "verification": self.verification,
        }


@dataclass
class ValidationResult:
    """Outcome of validating one configuration against the kernel (PRD §10)."""

    interface: str
    configured: dict[str, Any]
    measured: dict[str, float]
    within_tolerance: dict[str, bool]
    tolerance: float
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface": self.interface,
            "configured": self.configured,
            "measured": self.measured,
            "within_tolerance": self.within_tolerance,
            "tolerance": self.tolerance,
            "passed": self.passed,
            "measurement_point": "kernel tc qdisc readback (not end-to-end probe)",
        }


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class EmulationHarness:
    """
    Applies, verifies and removes tc/netem configurations for a run.

    Usage::

        harness = EmulationHarness(run_id="run-1")
        with harness.emulated(TcNetemConfig(interface="lo", delay_ms=50, seed=1),
                              link_id="A-B"):
            ...  # benchmark runs under the emulated conditions

        harness.records      # audit trail
    """

    def __init__(self, run_id: str, strict: bool = True):
        """
        Args:
            run_id: Correlates every applied configuration with the experiment.
            strict: Raise if verification fails. Set False to record the
                    mismatch and continue (useful when tuning tolerances).
        """
        self.run_id = run_id
        self.strict = strict
        self.records: list[EmulationRecord] = []

    # ------------------------------------------------------------------

    @contextmanager
    def emulated(
        self,
        cfg: TcNetemConfig,
        link_id: str = "",
        verify: bool = True,
    ) -> Iterator[EmulationRecord]:
        """
        Apply cfg for the duration of the block, then remove it.

        The qdisc is always torn down, including on exception, so a failed run
        cannot leave a host silently throttled for the next one.
        """
        record = self.apply(cfg, link_id=link_id, verify=verify)
        try:
            yield record
        finally:
            self.remove(cfg.interface)

    # ------------------------------------------------------------------

    def apply(self, cfg: TcNetemConfig, link_id: str = "", verify: bool = True) -> EmulationRecord:
        """Apply a configuration, verify it, and record the audit trail."""
        config_hash = apply_config(cfg)

        verification: dict[str, bool] = {}
        if verify:
            verification = verify_config(cfg)

        record = EmulationRecord(
            run_id=self.run_id,
            link_id=link_id,
            interface=cfg.interface,
            qdisc_params={
                k: v for k, v in cfg.__dict__.items() if v is not None
            },
            config_hash=config_hash,
            applied_at=datetime.utcnow(),
            verified=bool(verification) and all(verification.values()),
            verification=verification,
        )
        self.records.append(record)

        if verify and not record.verified:
            msg = (
                f"tc/netem verification failed for {cfg.interface}: {verification}"
            )
            if self.strict:
                # Undo the bad config before surfacing the failure.
                self.remove(cfg.interface)
                raise EmulationUnavailable(
                    msg,
                    "Loosen the tolerance, or check that another qdisc is not "
                    "already attached to this interface.",
                )
            log.warning(msg)

        log.info(
            "[emulation %s] applied %s hash=%s verified=%s",
            self.run_id, cfg.interface, config_hash[:12], record.verified,
        )
        return record

    def remove(self, interface: str) -> None:
        """Tear down the qdisc on an interface."""
        try:
            remove_config(interface)
        except Exception as exc:
            log.warning("[emulation %s] teardown of %s failed: %s", self.run_id, interface, exc)

    # ------------------------------------------------------------------

    def persist(self, db_session=None) -> int:
        """Write EmulationRecord rows to the TcConfig table (PRD §7)."""
        if db_session is None:
            return 0
        from netrescue.core.models import TcConfig

        written = 0
        for r in self.records:
            db_session.add(TcConfig(
                run_id=self.run_id,
                link_id=r.link_id or "unlinked",
                qdisc_params=r.qdisc_params,
                config_hash=r.config_hash,
                applied_at=r.applied_at,
                verified=r.verified,
            ))
            written += 1
        db_session.commit()
        return written


# ---------------------------------------------------------------------------
# Fault injection primitives (PRD §6.4)
# ---------------------------------------------------------------------------


class FaultInjector:
    """
    The four fault-injection primitives PRD §6.4 requires:

      * kill a node process
      * drop a link (``tc qdisc del``)
      * delay a link
      * rate-limit a link

    Each is reversible where reversal is meaningful; process kills obviously are
    not, so the caller is responsible for restarting.
    """

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.applied: list[tuple[str, TcNetemConfig | None]] = []

    # -- link-level ---------------------------------------------------

    def drop_link(self, interface: str) -> None:
        """Drop a link by deleting its qdisc. Link reverts to the default pfifo_fast."""
        try:
            remove_config(interface)
            self.applied.append((interface, None))
            log.info("[fault %s] dropped link %s", self.run_id, interface)
        except Exception as exc:
            log.warning("[fault %s] drop_link(%s) failed: %s", self.run_id, interface, exc)

    def delay_link(self, interface: str, delay_ms: float, jitter_ms: float | None = None,
                   seed: int | None = None) -> None:
        """Add fixed (optionally jittered) delay to a link."""
        cfg = TcNetemConfig(
            interface=interface, delay_ms=delay_ms, jitter_ms=jitter_ms, seed=seed
        )
        apply_config(cfg)
        self.applied.append((interface, cfg))
        log.info("[fault %s] delayed link %s by %.1fms", self.run_id, interface, delay_ms)

    def rate_limit_link(self, interface: str, rate_bps: float, queue_depth: int | None = None,
                        seed: int | None = None) -> None:
        """Token-bucket rate limit a link."""
        cfg = TcNetemConfig(
            interface=interface, rate_limit_bps=rate_bps,
            queue_depth=queue_depth, seed=seed,
        )
        apply_config(cfg)
        self.applied.append((interface, cfg))
        log.info("[fault %s] rate-limited link %s to %.0f bps", self.run_id, interface, rate_bps)

    def drop_packets(self, interface: str, loss_rate: float,
                     correlation: float | None = None, seed: int | None = None) -> None:
        """Random packet loss on a link."""
        cfg = TcNetemConfig(
            interface=interface, loss_rate=loss_rate,
            loss_correlation=correlation, seed=seed,
        )
        apply_config(cfg)
        self.applied.append((interface, cfg))
        log.info("[fault %s] loss %.2f%% on link %s", self.run_id, loss_rate * 100, interface)

    # -- process-level ------------------------------------------------

    def kill_node(self, pid: int, sig: int = 15) -> bool:
        """
        Terminate a forwarding node process (SIGTERM by default).

        Returns True if the signal was delivered. This is the primitive behind
        demo scenario 1 and the hysteresis sweep.
        """
        import os
        import signal

        try:
            os.kill(pid, sig)
            log.info("[fault %s] signalled node pid=%d sig=%d", self.run_id, pid, sig)
            return True
        except ProcessLookupError:
            log.warning("[fault %s] pid=%d not found", self.run_id, pid)
            return False
        except PermissionError:
            log.error("[fault %s] not permitted to signal pid=%d", self.run_id, pid)
            return False

    # -- cleanup ------------------------------------------------------

    def restore_all(self) -> None:
        """Remove every qdisc this injector applied."""
        for interface, _cfg in self.applied:
            try:
                remove_config(interface)
            except Exception as exc:
                log.warning("[fault %s] restore %s failed: %s", self.run_id, interface, exc)
        self.applied.clear()


# ---------------------------------------------------------------------------
# Validation (PRD §10, §11)
# ---------------------------------------------------------------------------


def validate_config(cfg: TcNetemConfig, tolerance: float = DEFAULT_TOLERANCE) -> ValidationResult:
    """
    Compare a configuration against what the kernel reports.

    This validates the *emulator*, not the workload: it reads back the qdisc
    parameters tc actually installed. It therefore proves the requested
    conditions were applied, and does not by itself prove end-to-end behaviour —
    that is what BenchmarkRunner measures.
    """
    require_emulation()
    checks = verify_config(cfg, tolerance=tolerance)

    configured = {
        "delay_ms": cfg.delay_ms,
        "loss_rate": cfg.loss_rate,
        "rate_limit_bps": cfg.rate_limit_bps,
        "queue_depth": cfg.queue_depth,
    }
    configured = {k: v for k, v in configured.items() if v is not None}

    return ValidationResult(
        interface=cfg.interface,
        configured=configured,
        measured={},  # populated by read_configured_qdisc when available
        within_tolerance=checks,
        tolerance=tolerance,
        passed=bool(checks) and all(checks.values()),
    )


def environment_report() -> dict[str, Any]:
    """Host capability snapshot, surfaced by the API (PRD §8.2 emulation lab)."""
    support = emulation_support()
    return {
        "emulation": support.to_dict(),
        "kernel": support.kernel_release,
        "note": (
            "Emulation requires Linux. Features A and C run on any platform."
        ),
    }


def interface_names() -> list[str]:
    """List network interfaces available for emulation."""
    support = emulation_support()
    if not support.usable:
        raise EmulationUnavailable(support.reason, support.hint)
    try:
        out = subprocess.run(
            ["ip", "-o", "link", "show"], capture_output=True, text=True, timeout=5, check=True
        )
    except Exception as exc:
        log.warning("ip link show failed: %s", exc)
        return []
    names = []
    for line in out.stdout.splitlines():
        parts = line.split(":")
        if len(parts) > 1:
            names.append(parts[1].strip().split("@")[0])
    return names