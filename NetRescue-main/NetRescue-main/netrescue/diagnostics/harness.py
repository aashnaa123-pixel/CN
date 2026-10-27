"""
netrescue/diagnostics/harness.py — the experiment harnesses and the A/B links.

This module is where Feature 3 connects to the two features it must not break:

  * **Feature A (hysteresis failure detection).** A real baseline measurement is
    captured before a recovery experiment and again after it, and the two are
    compared. The comparison is explicit that the diagnostics numbers are a real
    network measurement while Feature A's own link states come from the
    in-process simulator — they are not the same kind of evidence.
  * **Feature B (multipath transfer).** A transfer summary is paired with a
    diagnostics run and rendered side by side. Feature B's logical paths are
    labelled as logical, not as independent physical interfaces.

The hysteresis and scheduler harnesses reuse the existing, tested
``netrescue.benchmark`` implementations rather than reimplementing them. Both
produce *controlled* results: the conditions are modelled, and every report says
so.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Sequence

from netrescue.benchmark.comparison import (
    ALL_RETRANSMIT_POLICIES,
    ALL_VARIANTS,
    ComparisonConfig,
    PathSpec,
    SchedulerComparisonHarness,
)
from netrescue.benchmark.report import MIN_REPETITIONS
from netrescue.benchmark.sweep import DEFAULT_RATIOS, SweepConfig, HysteresisSweepHarness
from netrescue.diagnostics.config import SOFTWARE_VERSION, config_hash
from netrescue.diagnostics.probes import ProbeError, validate_target
from netrescue.diagnostics.runner import BenchmarkEngine, BenchmarkSpec

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature F — hysteresis sweep
# ---------------------------------------------------------------------------


async def hysteresis_sweep(
    *,
    ratios: Sequence[float] | None = None,
    f_thresh: int = 3,
    repetitions: int = MIN_REPETITIONS,
    seed: int = 20260101,
    total_intervals: int = 400,
) -> dict[str, Any]:
    """
    Sweep hysteresis ratios and report detection/recovery times and flap counts.

    The ratio range defaults to the PRD's 1.0–3.0 set. The scenario is seeded, so
    the same request produces the same table — and the result is labelled
    ``controlled`` because the heartbeat loss is modelled, not observed.
    """
    requested = list(ratios) if ratios else list(DEFAULT_RATIOS)
    if not requested:
        raise ValueError("at least one ratio is required")
    for ratio in requested:
        if not 1.0 <= float(ratio) <= 4.0:
            raise ValueError(f"ratio {ratio} is outside the supported 1.0–4.0 range")
    if not any(abs(float(r) - 1.0) < 1e-9 for r in requested):
        requested = [1.0, *requested]

    cfg = SweepConfig(
        run_id=f"sweep-{int(time.time())}",
        f_thresh=int(f_thresh),
        ratios=[float(r) for r in requested],
        repetitions=max(MIN_REPETITIONS, int(repetitions)),
        seed=int(seed),
        total_intervals=int(total_intervals),
    )
    report = await HysteresisSweepHarness(cfg).run()
    payload = report.to_dict()
    payload.update({
        "data_kind": "controlled",
        "simulated": True,
        "harness": "hysteresis_sweep",
        "config_hash": config_hash(cfg.workload()),
        "software_version": SOFTWARE_VERSION,
        "provenance": (
            "Hysteresis ratios are swept over a seeded heartbeat-loss sequence "
            "replayed for every ratio; the flap reduction is attributable to the "
            "dead band because the input is identical. The link behaviour is "
            "modelled, not captured from a real link."
        ),
    })
    return payload


# ---------------------------------------------------------------------------
# Feature G — scheduler comparison
# ---------------------------------------------------------------------------


def _path_specs(paths: Sequence[dict[str, Any]]) -> list[PathSpec]:
    if not paths:
        # PRD's 5:1 heterogeneity default so the comparison is meaningful.
        return [
            PathSpec(path_id="P1", fd_ms=10.0, tp_bps=1_000_000.0, loss_rate=0.0),
            PathSpec(path_id="P2", fd_ms=50.0, tp_bps=2_000_000.0, loss_rate=0.01),
            PathSpec(path_id="P3", fd_ms=25.0, tp_bps=500_000.0, loss_rate=0.0),
        ]
    specs: list[PathSpec] = []
    for item in paths:
        specs.append(PathSpec(
            path_id=str(item.get("path_id", f"P{len(specs) + 1}")),
            fd_ms=float(item.get("fd_ms", 10.0)),
            tp_bps=float(item.get("tp_bps", 1_000_000.0)),
            loss_rate=float(item.get("loss_rate", 0.0)),
            cwnd_bytes=float(item.get("cwnd_bytes", 65536.0)),
        ))
    return specs


async def scheduler_comparison(
    *,
    paths: Sequence[dict[str, Any]] | None = None,
    payload_bytes: int = 256_000,
    chunk_size_bytes: int = 4096,
    repetitions: int = MIN_REPETITIONS,
    variants: Sequence[str] | None = None,
    retransmit_policies: Sequence[str] | None = None,
    seed: int | None = 1337,
) -> dict[str, Any]:
    """
    Compare scheduler variants over an identical, declarative path set.

    Every variant is rebuilt from the same ``PathSpec`` list so no estimate
    leaks between cells, and each cell is repeated at least five times. This is
    a *controlled* benchmark over modelled path conditions — the paths are
    logical, not physical interfaces on this machine.
    """
    chosen_variants = [str(v) for v in (variants or ALL_VARIANTS)]
    unknown = [v for v in chosen_variants if v not in ALL_VARIANTS]
    if unknown:
        raise ValueError(
            f"unknown scheduler variant(s) {unknown}; valid variants are "
            f"{', '.join(ALL_VARIANTS)}"
        )
    chosen_policies = [str(p) for p in (retransmit_policies or ALL_RETRANSMIT_POLICIES)]
    unknown_policies = [p for p in chosen_policies if p not in ALL_RETRANSMIT_POLICIES]
    if unknown_policies:
        raise ValueError(
            f"unknown retransmission policy(ies) {unknown_policies}; valid "
            f"policies are {', '.join(ALL_RETRANSMIT_POLICIES)}"
        )

    cfg = ComparisonConfig(
        run_id=f"cmp-{int(time.time())}",
        paths=_path_specs(list(paths) if paths else []),
        payload_bytes=int(payload_bytes),
        chunk_size_bytes=int(chunk_size_bytes),
        repetitions=max(MIN_REPETITIONS, int(repetitions)),
        variants=chosen_variants,
        retransmit_policies=chosen_policies,
        seed=seed,
    )
    report = await SchedulerComparisonHarness(cfg).run()
    payload = report.to_dict()
    payload.update({
        "data_kind": "controlled",
        "simulated": True,
        "harness": "scheduler_comparison",
        "config_hash": config_hash(cfg.workload()),
        "software_version": SOFTWARE_VERSION,
        "provenance": (
            "Scheduler and retransmission variants run over identical declarative "
            "path conditions. The paths are logical, not independent physical "
            "interfaces; the comparison isolates the scheduling policy."
        ),
    })
    return payload


# ---------------------------------------------------------------------------
# Feature A integration
# ---------------------------------------------------------------------------


async def capture_baseline_measurement(
    *,
    phase: str,
    target: str,
    count: int = 4,
    timeout_s: float = 2.0,
    repetitions: int = 1,
    label: str = "",
) -> dict[str, Any]:
    """
    Capture a real baseline (or after-experiment) network measurement.

    ``phase`` is ``before`` or ``after``. The result is a real ping benchmark
    annotated with the phase and a note separating it from Feature A's
    simulator, so the two are never conflated in a report.
    """
    if phase not in ("before", "after"):
        raise ValueError("phase must be 'before' or 'after'")
    validate_target(target)

    spec = BenchmarkSpec(
        target=target,
        count=int(count),
        timeout_s=float(timeout_s),
        repetitions=int(repetitions),
        label=label or f"feature-a-{phase}",
    )
    run = await BenchmarkEngine(spec).run()
    payload = run.to_dict()
    payload.update({
        "kind": "feature_a_baseline",
        "phase": phase,
        "annotation": {
            "source": "real_ping",
            "data_kind": "real",
            "label": label,
            "feature_a_relationship": (
                "This is a real measurement of this machine's network taken to "
                "bracket a Feature A experiment. Feature A's own link states are "
                "produced by its in-process simulator and are a different kind of "
                "evidence; a healthy baseline here does not validate the simulator."
            ),
        },
    })
    return payload


def compare_baseline(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """
    Compare a before/after pair without inventing causality.

    The delta is arithmetic on real measurements. Whether a Feature A experiment
    caused it is a separate question the data alone cannot answer, and the report
    says so.
    """
    def agg(run: dict[str, Any]) -> dict[str, Any]:
        return run.get("aggregate") or {}

    b, a = agg(before), agg(after)
    delta: dict[str, Any] = {}
    for key in ("avg_ms", "min_ms", "max_ms", "stddev_ms", "packet_loss_rate"):
        bv, av = b.get(key), a.get(key)
        if bv is None or av is None:
            delta[key] = None
        elif key == "packet_loss_rate":
            delta[key] = {
                "before": bv,
                "after": av,
                "delta": round(av - bv, 6),
                "delta_pct_points": f"{(av - bv) * 100:+.2f} pp",
            }
        else:
            delta[key] = {
                "before": bv,
                "after": av,
                "delta_ms": round(av - bv, 3),
            }

    return {
        "kind": "feature_a_baseline_comparison",
        "before": {
            "run_id": before.get("run_id"),
            "phase": before.get("phase"),
            "config_hash": before.get("config_hash"),
            "aggregate": b,
        },
        "after": {
            "run_id": after.get("run_id"),
            "phase": after.get("phase"),
            "config_hash": after.get("config_hash"),
            "aggregate": a,
        },
        "delta": delta,
        "same_configuration": before.get("config_hash") == after.get("config_hash"),
        "interpretation": (
            "The delta is arithmetic on two real measurements. A Feature A "
            "experiment may coincide with it, but this comparison does not by "
            "itself establish that the experiment caused the change — the network "
            "also moves on its own. Feature A's simulator state is reported "
            "separately and is not evidence about this network."
        ),
        "generated_at_utc": _utc_now(),
    }


# ---------------------------------------------------------------------------
# Feature B integration
# ---------------------------------------------------------------------------


def feature_b_side_by_side(
    diagnostics_run: dict[str, Any],
    transfer: dict[str, Any],
) -> dict[str, Any]:
    """
    Pair a Feature B transfer summary with a diagnostics run, labelled.

    The two columns are different kinds of object — a real ICMP measurement and
    a chunk-scheduling experiment over logical paths — so they are shown beside
    each other, never merged.
    """
    diag_agg = diagnostics_run.get("aggregate") or (diagnostics_run.get("benchmark") or {}).get("aggregate") or {}
    diag_config = diagnostics_run.get("config") or {}

    def pick(source: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if source.get(key) is not None:
                return source[key]
        return None

    return {
        "kind": "feature_b_diagnostics_pair",
        "diagnostics": {
            "label": "Real network diagnostics",
            "data_kind": "real" if not diagnostics_run.get("simulated") else "simulated",
            "run_id": diagnostics_run.get("run_id"),
            "target": diag_config.get("target"),
            "config_hash": diagnostics_run.get("config_hash"),
            "metrics": {
                "avg_ms": diag_agg.get("avg_ms"),
                "min_ms": diag_agg.get("min_ms"),
                "max_ms": diag_agg.get("max_ms"),
                "stddev_ms": diag_agg.get("stddev_ms"),
                "packet_loss_pct": (
                    f"{diag_agg['packet_loss_rate'] * 100:.2f}"
                    if diag_agg.get("packet_loss_rate") is not None else None
                ),
                "sent": diag_agg.get("sent"),
                "received": diag_agg.get("received"),
            },
            "units": {"latency": "ms", "loss": "percent"},
        },
        "transfer": {
            "label": "Feature B multipath transfer (logical paths)",
            "data_kind": "controlled",
            "run_id": pick(transfer, "transfer_id", "run_id"),
            "scheduler_variant": transfer.get("scheduler_variant"),
            "retransmit_policy": transfer.get("retransmit_policy"),
            "metrics": {
                "total_chunks": transfer.get("total_chunks"),
                "delivered_chunks": transfer.get("delivered_chunks"),
                "failed_chunks": transfer.get("failed_chunks"),
                "duration_s": transfer.get("duration_s"),
                "goodput_bps": transfer.get("goodput_bps"),
                "retransmission_count": transfer.get("retransmission_count"),
                "out_of_order_count": transfer.get("out_of_order_count"),
                "integrity_ok": transfer.get("integrity_ok"),
                "per_path_utilisation": transfer.get("per_path_utilisation"),
            },
            "units": {"goodput": "bps", "duration": "s"},
        },
        "notes": [
            "The diagnostics column is a real ICMP measurement of this machine's "
            "network. The transfer column is a chunk-scheduling experiment.",
            "Feature B's paths are logical paths, not independent physical "
            "interfaces, and are not measured as separate real sockets.",
            "Being in the same report does not mean the two numbers measure the "
            "same thing or should be added together.",
        ],
        "generated_at_utc": _utc_now(),
    }


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
