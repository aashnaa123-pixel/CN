"""
netrescue/emulation/tc_netem.py

Linux tc/netem wrapper for per-path network emulation (PRD §6.3).

Supports all 9 emulation parameters:
  delay, jitter, loss, duplication, corruption, reordering,
  rate_limit, queue_depth, seed

Usage::

    from netrescue.emulation.tc_netem import TcNetemConfig, apply_config, verify_config

    cfg = TcNetemConfig(interface="eth0", delay_ms=50, jitter_ms=10, loss_rate=0.01, seed=42)
    apply_config(cfg)
    verify_config(cfg)

NOTE: tc/netem commands require CAP_NET_ADMIN. Run with sudo or grant the
capability to the node process. Node processes themselves run as non-root;
only tc configuration is privileged (PRD §9 rule 7).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from netrescue.emulation.platform import EmulationUnavailable, require_emulation

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------


@dataclass
class TcNetemConfig:
    """All 9 tc/netem emulation parameters for a single network interface."""

    interface: str
    delay_ms: float | None = None          # Added delay in milliseconds
    jitter_ms: float | None = None         # Jitter ± milliseconds
    delay_correlation: float | None = None  # Delay correlation (0–100%)
    loss_rate: float | None = None         # Packet loss rate (0.0–1.0)
    loss_correlation: float | None = None  # Loss correlation (0–100%)
    duplication_rate: float | None = None  # Duplication rate (0.0–1.0)
    corruption_rate: float | None = None   # Corruption/bit-error rate
    reorder_rate: float | None = None      # Packet reordering rate
    reorder_gap: int | None = None         # gap parameter for reordering
    rate_limit_bps: float | None = None    # Token-bucket rate limit in bits/s
    queue_depth: int | None = None         # Max packets in qdisc queue
    seed: int | None = None                # Deterministic seed for reproducibility

    def sha256(self) -> str:
        """SHA-256 hash of the config for audit logging (PRD §6.3)."""
        payload = json.dumps(self.__dict__, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def to_tc_args(self) -> list[str]:
        """Build the tc qdisc netem argument list."""
        args: list[str] = ["netem"]

        # delay
        if self.delay_ms is not None:
            delay_arg = f"{self.delay_ms}ms"
            if self.jitter_ms is not None:
                delay_arg += f" {self.jitter_ms}ms"
                if self.delay_correlation is not None:
                    delay_arg += f" {self.delay_correlation}%"
            args += ["delay", delay_arg]

        # loss
        if self.loss_rate is not None:
            loss_pct = f"{self.loss_rate * 100:.4f}%"
            if self.loss_correlation is not None:
                loss_pct += f" {self.loss_correlation}%"
            args += ["loss", "random", loss_pct]

        # duplication
        if self.duplication_rate is not None:
            args += ["duplicate", f"{self.duplication_rate * 100:.4f}%"]

        # corruption
        if self.corruption_rate is not None:
            args += ["corrupt", f"{self.corruption_rate * 100:.4f}%"]

        # reorder
        if self.reorder_rate is not None:
            reorder_pct = f"{self.reorder_rate * 100:.4f}%"
            if self.reorder_gap is not None:
                reorder_pct += f" gap {self.reorder_gap}"
            args += ["reorder", reorder_pct]

        # rate limit
        if self.rate_limit_bps is not None:
            args += ["rate", f"{int(self.rate_limit_bps)}bit"]

        # queue depth
        if self.queue_depth is not None:
            args += ["limit", str(self.queue_depth)]

        # seed (deterministic mode)
        if self.seed is not None:
            args += ["seed", str(self.seed)]

        return args


# ---------------------------------------------------------------------------
# Apply / remove helpers
# ---------------------------------------------------------------------------


def _run_tc(*args: str) -> str:
    """
    Run a tc subcommand, returning stdout. Raises on failure.

    Requires CAP_NET_ADMIN (or root). Emulation lives in the Linux kernel, so
    this raises EmulationUnavailable with an actionable hint on non-Linux hosts
    instead of an opaque FileNotFoundError from subprocess.
    """
    require_emulation()

    cmd = ["tc"] + list(args)
    log.debug("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10
        )
    except FileNotFoundError as exc:  # tc vanished between probe and call
        raise EmulationUnavailable(
            f"`tc` could not be executed: {exc}",
            "Install iproute2, or run on Linux/WSL2.",
        ) from exc
    except PermissionError as exc:
        raise EmulationUnavailable(
            f"permission denied running `{' '.join(cmd)}`: {exc}",
            "Grant CAP_NET_ADMIN, or run the tc step under sudo only.",
        ) from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"tc failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def _qdisc_exists(interface: str) -> bool:
    """Return True if a netem qdisc is already on the interface."""
    try:
        out = _run_tc("qdisc", "show", "dev", interface)
        return "netem" in out
    except RuntimeError:
        return False


def apply_config(cfg: TcNetemConfig) -> str:
    """
    Apply tc/netem config to an interface.

    Uses 'tc qdisc add' on first call; 'tc qdisc change' if netem already exists.
    Returns the config hash for storage in TcConfig table.

    Raises RuntimeError if tc command fails (e.g. missing CAP_NET_ADMIN).
    """
    action = "change" if _qdisc_exists(cfg.interface) else "add"
    base_args = ["qdisc", action, "dev", cfg.interface, "root", "handle", "1:"]
    netem_args = cfg.to_tc_args()
    _run_tc(*(base_args + netem_args))

    config_hash = cfg.sha256()
    log.info(
        "Applied tc/netem to %s: %s … (hash=%s)",
        cfg.interface,
        " ".join(netem_args[:6]),
        config_hash[:12],
    )
    return config_hash


def remove_config(interface: str) -> None:
    """Remove the netem qdisc from an interface (fault injection / cleanup)."""
    if _qdisc_exists(interface):
        _run_tc("qdisc", "del", "dev", interface, "root")
        log.info("Removed tc/netem qdisc from %s", interface)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_config(cfg: TcNetemConfig, tolerance: float = 0.10) -> dict[str, bool]:
    """
    Read back the applied qdisc and compare key parameters.

    Returns a dict mapping parameter name → within_tolerance (bool).
    Logs a warning for any value outside the ±tolerance band.

    PRD §6.7: measured values must be within documented tolerance of configured values.
    """
    try:
        out = _run_tc("qdisc", "show", "dev", cfg.interface)
    except RuntimeError as exc:
        log.error("verify_config failed: %s", exc)
        return {}

    results: dict[str, bool] = {}

    # --- delay ---
    if cfg.delay_ms is not None:
        m = re.search(r"delay\s+([\d.]+)ms", out)
        if m:
            measured = float(m.group(1))
            ok = abs(measured - cfg.delay_ms) / max(cfg.delay_ms, 1e-9) <= tolerance
            results["delay_ms"] = ok
            if not ok:
                log.warning(
                    "delay_ms mismatch: configured=%.1f measured=%.1f (%.1f%% error)",
                    cfg.delay_ms, measured, abs(measured - cfg.delay_ms) / cfg.delay_ms * 100,
                )
        else:
            results["delay_ms"] = False
            log.warning("delay_ms not found in tc output for %s", cfg.interface)

    # --- loss ---
    if cfg.loss_rate is not None:
        m = re.search(r"loss\s+([\d.]+)%", out)
        if m:
            measured_pct = float(m.group(1))
            configured_pct = cfg.loss_rate * 100
            ok = abs(measured_pct - configured_pct) / max(configured_pct, 1e-9) <= tolerance
            results["loss_rate"] = ok
            if not ok:
                log.warning(
                    "loss_rate mismatch: configured=%.4f%% measured=%.4f%%",
                    configured_pct, measured_pct,
                )
        else:
            results["loss_rate"] = False

    log.info("Verification results for %s: %s", cfg.interface, results)
    return results
