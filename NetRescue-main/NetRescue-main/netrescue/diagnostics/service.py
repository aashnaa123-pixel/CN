"""
netrescue/diagnostics/service.py — the composed diagnostics run.

One call gathers what Feature 3's Live Network Status, Latency & Packet Loss,
Route Trace and Adapter panels need, so the "Run Diagnostics" button performs a
single real operation and renders one record.

Every section is independent and degrades on its own: if the route trace times
out, the ping statistics and the adapter list are still returned, and the bundle
records which sections are real, which are unavailable and why. A partially
successful run is a success, not an error.
"""

from __future__ import annotations

import logging
import platform
import socket
import time
import uuid
from typing import Any

from netrescue.diagnostics.config import SOFTWARE_VERSION, config_hash
from netrescue.diagnostics.probes import (
    NETWORK_POLICY_NOTES,
    ProbeError,
    dns_lookup,
    network_status,
    ping,
    traceroute,
    validate_target,
)

log = logging.getLogger(__name__)


async def run_full_diagnostics(
    *,
    target: str,
    count: int = 4,
    timeout_s: float = 2.0,
    include_traceroute: bool = True,
    max_hops: int = 8,
    trace_timeout_s: float = 1.5,
    include_adapters: bool = True,
    dns_name: str | None = None,
    dns_timeout_s: float = 5.0,
) -> dict[str, Any]:
    """
    Run a real diagnostics bundle and return one record with per-section status.

    The ping section is required: if the target itself is invalid the whole call
    fails with ``ValueError``. Everything else is best-effort and reports its own
    failure in ``sections``.
    """
    import asyncio

    host = validate_target(target)
    started = time.time()
    run_id = f"diag-{uuid.uuid4().hex[:10]}"

    snapshot = {
        "kind": "full_diagnostics",
        "target": host,
        "count": int(count),
        "timeout_s": float(timeout_s),
        "include_traceroute": bool(include_traceroute),
        "max_hops": int(max_hops),
        "trace_timeout_s": float(trace_timeout_s),
        "include_adapters": bool(include_adapters),
        "dns_name": dns_name,
        "software_version": SOFTWARE_VERSION,
        "seed": None,
        "deterministic": False,
    }

    sections: dict[str, Any] = {}

    # --- ping (required) ----------------------------------------------------
    try:
        ping_result = await asyncio.to_thread(
            ping, host, count=int(count), timeout_s=float(timeout_s),
        )
        sections["ping"] = {"status": "ok", "data": ping_result.to_dict()}
    except ProbeError as exc:
        sections["ping"] = {"status": "error", "error": str(exc), "kind": exc.kind, "hint": exc.hint}

    # --- adapters / IP config (best effort) ---------------------------------
    if include_adapters:
        try:
            status_data = await asyncio.to_thread(network_status)
            sections["network_status"] = {"status": "ok", "data": status_data}
        except Exception as exc:  # noqa: BLE001 - a status failure must not sink the run
            sections["network_status"] = {"status": "error", "error": str(exc)}
    else:
        sections["network_status"] = {"status": "skipped", "reason": "disabled for this run"}

    # --- traceroute (best effort) -------------------------------------------
    if include_traceroute:
        try:
            trace_data = await asyncio.to_thread(
                traceroute, host, max_hops=int(max_hops), timeout_s=float(trace_timeout_s),
            )
            sections["traceroute"] = {"status": "ok", "data": trace_data}
        except ProbeError as exc:
            sections["traceroute"] = {
                "status": "error", "error": str(exc), "kind": exc.kind, "hint": exc.hint,
            }
    else:
        sections["traceroute"] = {"status": "skipped", "reason": "disabled for this run"}

    # --- DNS (best effort) --------------------------------------------------
    if dns_name:
        try:
            sections["dns"] = {
                "status": "ok",
                "data": await asyncio.to_thread(dns_lookup, dns_name, timeout_s=float(dns_timeout_s)),
            }
        except ProbeError as exc:
            sections["dns"] = {"status": "error", "error": str(exc), "kind": exc.kind, "hint": exc.hint}
    else:
        sections["dns"] = {"status": "skipped", "reason": "no DNS name requested"}

    ok_sections = [k for k, v in sections.items() if v.get("status") == "ok"]
    error_sections = [k for k, v in sections.items() if v.get("status") == "error"]

    ping_data = sections.get("ping", {}).get("data") or {}
    run = {
        "run_id": run_id,
        "kind": "full_diagnostics",
        "source": "real",
        "simulated": False,
        "data_kind": "real",
        "status": "DONE" if sections.get("ping", {}).get("status") == "ok" and not error_sections
        else ("PARTIAL" if ok_sections else "FAILED"),
        "config": snapshot,
        "config_hash": config_hash(snapshot),
        "software_version": SOFTWARE_VERSION,
        "started_at": started,
        "generated_at": started,
        "duration_s": round(time.time() - started, 3),
        "sections": sections,
        "ok_sections": ok_sections,
        "error_sections": error_sections,
        # A compact aggregate so the run flows through the same history, export
        # and comparison machinery as a plain benchmark.
        "aggregate": {
            "sent": ping_data.get("sent"),
            "received": ping_data.get("received"),
            "lost": (
                max(0, (ping_data.get("sent") or 0) - (ping_data.get("received") or 0))
                if ping_data else None
            ),
            "packet_loss_rate": ping_data.get("loss_rate"),
            "min_ms": ping_data.get("min_ms"),
            "avg_ms": ping_data.get("avg_ms"),
            "max_ms": ping_data.get("max_ms"),
            "stddev_ms": ping_data.get("stddev_ms"),
        },
        "environment": {
            "os": (sections.get("network_status", {}).get("data") or {}).get("platform")
            or platform.system(),
            "release": (sections.get("network_status", {}).get("data") or {}).get("release")
            or platform.release(),
            "hostname": (sections.get("network_status", {}).get("data") or {}).get("hostname")
            or socket.gethostname(),
            "software_version": SOFTWARE_VERSION,
        },
        "limitations": list(NETWORK_POLICY_NOTES) + [
            "Sections are independent: a failed route trace does not invalidate "
            "the ping statistics.",
            "Adapter enumeration requires the Windows Get-Net* cmdlets; without "
            "them that section reports why it is unavailable.",
        ],
    }
    log.info(
        "[diag %s] full diagnostics target=%s ok=%s errors=%s",
        run_id, host, ok_sections, error_sections,
    )
    return run
