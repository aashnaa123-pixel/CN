"""
apps/api/routers/diagnostics.py — Feature 3: Network Diagnostics & Benchmarking Lab.

This router replaces the Linux-only `tc`/`netem` emulation control surface. It
runs real operating-system network commands on Windows 10/11 unprivileged, adds
a deterministic simulator for fault behaviour, and connects the result to
Feature A (recovery) and Feature B (multipath transfer) without breaking either.

Endpoints
---------
Read-only, no artifact produced (Permission.READ):

  GET  /api/diagnostics/status           capability report: real / controlled / unsupported
  GET  /api/diagnostics/scenarios        the simulated fault scenarios and their knobs
  GET  /api/diagnostics/network          adapters, IP configuration, default gateway
  POST /api/diagnostics/ping             real ping, latency + loss statistics
  POST /api/diagnostics/traceroute       real route trace
  POST /api/diagnostics/dns              real DNS lookup with duration
  GET  /api/diagnostics/runs             stored run history (summaries)
  GET  /api/diagnostics/runs/{id}        one stored run
  GET  /api/diagnostics/runs/{id}/report measurement report
  GET  /api/diagnostics/runs/{id}/export JSON or CSV
  POST /api/diagnostics/runs/compare     side-by-side comparison, no combined score

Operator-only (Permission.WRITE), because each creates or persists a run:

  POST /api/diagnostics/run              the composed "Run Diagnostics" bundle
  POST /api/diagnostics/benchmarks       repeatable real ping benchmark
  POST /api/diagnostics/faults           deterministic simulated fault scenario
  POST /api/diagnostics/hysteresis-sweep Feature F: ratio sweep + trade-off
  POST /api/diagnostics/scheduler-comparison  Feature G: variant comparison
  POST /api/diagnostics/integrations/feature-a/capture
  POST /api/diagnostics/integrations/feature-a/compare
  POST /api/diagnostics/integrations/feature-b

Every input is validated, every subprocess is bounded, and a partial run is
reported as partial rather than dressed up as a success.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from netrescue.core.config import settings
from apps.api.deps import OptionalDbSession, RequireHostProbe, RequireOperator, RequireRead
from netrescue.diagnostics import (
    SCENARIO_NAMES,
    BenchmarkEngine,
    BenchmarkSpec,
    FaultScenario,
    ProbeError,
    capture_baseline_measurement,
    compare_baseline,
    compare_runs,
    diagnostics_store,
    dns_lookup,
    feature_b_side_by_side,
    hysteresis_sweep,
    measurement_report,
    network_status,
    persist_run,
    ping,
    probe_capabilities,
    run_fault_scenario,
    run_full_diagnostics,
    run_to_csv,
    run_to_json,
    scheduler_comparison,
    suggested_target,
    traceroute,
)

log = logging.getLogger(__name__)

from netrescue.labs import execution

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: Map a probe failure kind onto the HTTP status that describes it.
_STATUS_BY_KIND = {
    "invalid_input": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "unsupported": status.HTTP_503_SERVICE_UNAVAILABLE,
    "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    "command_failed": status.HTTP_502_BAD_GATEWAY,
}


def _http_error(exc: ProbeError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_BY_KIND.get(exc.kind, status.HTTP_502_BAD_GATEWAY),
        detail={"error": str(exc), "kind": exc.kind, "hint": exc.hint},
    )


def _store(db, run: dict) -> dict:
    """Persist best-effort, then record in the in-process history."""
    run["persisted"] = persist_run(db, run)
    diagnostics_store.record(run)
    return run


def _require_run(run_id: str) -> dict:
    run = diagnostics_store.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No diagnostics run {run_id!r} is stored. Run history is "
                   "in-process and is cleared when the API restarts.",
        )
    return run


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PingRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=253, description="IPv4 address or hostname")
    count: int = Field(4, ge=1, le=100, description="Number of probes")
    timeout_s: float = Field(2.0, gt=0, le=60, description="Per-probe wait, seconds")
    size_bytes: int | None = Field(None, ge=8, le=65500, description="Optional payload size")


class TraceRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=253)
    max_hops: int = Field(20, ge=1, le=64)
    timeout_s: float = Field(3.0, gt=0, le=60)


class DnsRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=253)
    server: str | None = Field(None, max_length=253, description="Ask this resolver specifically")
    timeout_s: float = Field(5.0, gt=0, le=60)


class RunDiagnosticsRequest(BaseModel):
    target: str | None = Field(None, max_length=253, description="Defaults to the default gateway, then loopback")
    count: int = Field(4, ge=1, le=100)
    timeout_s: float = Field(2.0, gt=0, le=60)
    include_traceroute: bool = True
    max_hops: int = Field(8, ge=1, le=64)
    trace_timeout_s: float = Field(1.5, gt=0, le=60)
    include_adapters: bool = True
    dns_name: str | None = Field(None, max_length=253)
    dns_timeout_s: float = Field(5.0, gt=0, le=60)


class BenchmarkRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=253)
    count: int = Field(4, ge=1, le=100, description="Probes per repetition")
    timeout_s: float = Field(2.0, gt=0, le=60)
    repetitions: int = Field(3, ge=1, le=50)
    interval_s: float = Field(0.0, ge=0, le=60, description="Pause between repetitions")
    label: str = Field("", max_length=120)


class FaultRequest(BaseModel):
    scenario: str = Field("packet_loss", description=f"One of: {', '.join(SCENARIO_NAMES)}")
    seed: int = Field(20260101, description="Fixed seed ⇒ reproducible simulation")
    count: int = Field(5, ge=1, le=100)
    repetitions: int = Field(3, ge=1, le=50)
    timeout_s: float = Field(1.0, gt=0, le=60)
    latency_ms: float = Field(20.0, ge=0, le=60000)
    jitter_ms: float = Field(5.0, ge=0, le=60000)
    loss_rate: float = Field(0.2, ge=0.0, le=1.0)
    period: int = Field(6, ge=2, le=100, description="On/off window for the intermittent scenario")
    target: str = Field("simulated-host", max_length=253)


class HysteresisSweepRequest(BaseModel):
    ratios: list[float] | None = Field(None, description="Defaults to 1.0, 1.5, 2.0, 2.5, 3.0")
    f_thresh: int = Field(3, ge=1, le=20)
    repetitions: int = Field(5, ge=1, le=50)
    seed: int = 20260101
    total_intervals: int = Field(400, ge=50, le=5000)


class PathIn(BaseModel):
    path_id: str = Field(..., min_length=1, max_length=32)
    fd_ms: float = Field(10.0, gt=0, le=60000)
    tp_bps: float = Field(1_000_000.0, gt=0)
    loss_rate: float = Field(0.0, ge=0.0, le=1.0)
    cwnd_bytes: float = Field(65536.0, gt=0)


class SchedulerComparisonRequest(BaseModel):
    paths: list[PathIn] | None = None
    payload_bytes: int = Field(256_000, ge=1024, le=8_000_000)
    chunk_size_bytes: int = Field(4096, ge=64, le=1_000_000)
    repetitions: int = Field(5, ge=1, le=50)
    variants: list[str] | None = None
    retransmit_policies: list[str] | None = None
    seed: int | None = 1337


# Public-demo bounds. The comparison runs modelled delays in real time (about
# 0.4 s per kB of payload per repetition), so these keep one run near a minute,
# inside a hosting proxy's ~100 s limit.
DEMO_MAX_PAYLOAD_BYTES = 64_000
DEMO_MAX_REPETITIONS = 10
DEMO_MAX_PATHS = 8
_scheduler_comparison_lock = asyncio.Lock()


def _enforce_demo_limits(**limits: tuple[int, int]) -> None:
    if not settings.public_demo:
        return
    for name, (value, maximum) in limits.items():
        if value > maximum:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"{name} is limited to {maximum} on the public demo (requested {value}). "
                    "Run NetRescue locally for larger runs."
                ),
            )


class CompareRequest(BaseModel):
    run_ids: list[str] = Field(..., min_length=2, max_length=10)


class FeatureABaselineRequest(BaseModel):
    phase: str = Field(..., pattern="^(before|after)$")
    target: str | None = Field(None, max_length=253)
    count: int = Field(4, ge=1, le=100)
    timeout_s: float = Field(2.0, gt=0, le=60)
    repetitions: int = Field(1, ge=1, le=20)
    label: str = Field("", max_length=120)


class FeatureACompareRequest(BaseModel):
    before_run_id: str
    after_run_id: str


class FeatureBPairRequest(BaseModel):
    diagnostics_run_id: str
    transfer: dict = Field(..., description="A Feature B transfer summary")


# ---------------------------------------------------------------------------
# Capability / status
# ---------------------------------------------------------------------------


@router.get("/status")
def diagnostics_status(principal: RequireRead):
    """
    What this host can measure for real, what is modelled, and what is not
    supported — served verbatim so the UI can label every number honestly.
    """
    caps = probe_capabilities()
    caps["suggested_target"] = suggested_target()
    caps["history"] = {
        "stored_runs": diagnostics_store.count(),
        "max_history": diagnostics_store.max_history,
        "note": "Run history is in-process and is cleared when the API restarts.",
    }
    return caps


@router.get("/scenarios")
def diagnostics_scenarios(principal: RequireRead):
    """The deterministic simulated fault scenarios and their default parameters."""
    return {
        "scenarios": [
            {"name": "unreachable", "description": "Every probe fails (black hole / host down)."},
            {"name": "latency_jitter", "description": "Replies arrive, slowly and inconsistently."},
            {"name": "packet_loss", "description": "A steady fraction of probes is dropped."},
            {"name": "timeout", "description": "Modelled latency exceeds the probe timeout."},
            {"name": "intermittent", "description": "Alternating reachable/unreachable windows."},
            {"name": "healthy_to_unhealthy", "description": "Healthy, then degraded, then recovered."},
            {"name": "transitions", "description": "Healthy/unhealthy transitions for the state view."},
        ],
        "seed_control": "A fixed seed makes a scenario reproducible; it cannot make a "
                        "real network deterministic.",
        "all_simulated": True,
    }


@router.get("/network")
def diagnostics_network(principal: RequireHostProbe):
    """Real adapter and IP configuration for the Live Network Status panel."""
    return execution.attach(dict(network_status()), "diag.network")


# ---------------------------------------------------------------------------
# Individual real probes
# ---------------------------------------------------------------------------


@router.post("/ping")
async def diagnostics_ping(body: PingRequest, principal: RequireHostProbe):
    """Ping an IPv4 address or hostname and return real latency/loss statistics."""
    import asyncio

    try:
        response = await asyncio.to_thread(
            ping, body.target, count=body.count, timeout_s=body.timeout_s,
            size_bytes=body.size_bytes,
        )
    except ProbeError as exc:
        raise _http_error(exc)
    return execution.attach(response.to_dict(), "diag.ping", target=body.target)


@router.post("/traceroute")
async def diagnostics_traceroute(body: TraceRequest, principal: RequireHostProbe):
    """Trace the route to a target and return the real hops."""
    import asyncio

    try:
        result = await asyncio.to_thread(
            traceroute, body.target, max_hops=body.max_hops, timeout_s=body.timeout_s,
        )
    except ProbeError as exc:
        raise _http_error(exc)
    return execution.attach(dict(result), "diag.traceroute", target=body.target)


@router.post("/dns")
async def diagnostics_dns(body: DnsRequest, principal: RequireHostProbe):
    """Resolve a name and report the addresses and how long resolution took."""
    import asyncio

    try:
        result = await asyncio.to_thread(
            dns_lookup, body.name, server=body.server, timeout_s=body.timeout_s,
        )
    except ProbeError as exc:
        raise _http_error(exc)
    return execution.attach(dict(result), "diag.dns", target=body.name)


# ---------------------------------------------------------------------------
# The composed run — the "Run Diagnostics" button
# ---------------------------------------------------------------------------


@router.post("/run")
async def diagnostics_run(body: RunDiagnosticsRequest, db: OptionalDbSession, principal: RequireOperator):
    """
    Run the composed diagnostics bundle and store it.

    A single real operation that fills the Live Network Status, Latency & Packet
    Loss, Route Trace and Adapter panels. Individual sections degrade on their
    own; the bundle's ``status`` says which happened.
    """
    target = body.target or suggested_target()
    try:
        run = await run_full_diagnostics(
            target=target,
            count=body.count,
            timeout_s=body.timeout_s,
            include_traceroute=body.include_traceroute,
            max_hops=body.max_hops,
            trace_timeout_s=body.trace_timeout_s,
            include_adapters=body.include_adapters,
            dns_name=body.dns_name,
            dns_timeout_s=body.dns_timeout_s,
        )
    except ProbeError as exc:
        raise _http_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    out = execution.attach(dict(_store(db, run)), "diag.ping", target=target,
                           outcome=str(run.get("status", "")) or None)
    out["execution_sections"] = {
        "ping": execution.describe("diag.ping"), "traceroute": execution.describe("diag.traceroute"),
        "dns": execution.describe("diag.dns"), "adapters": execution.describe("diag.network"),
    }
    return out


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


@router.post("/benchmarks", status_code=status.HTTP_201_CREATED)
async def diagnostics_benchmark(body: BenchmarkRequest, db: OptionalDbSession, principal: RequireOperator):
    """Run a repeatable real ping benchmark and store the structured result."""
    spec = BenchmarkSpec(
        target=body.target,
        count=body.count,
        timeout_s=body.timeout_s,
        repetitions=body.repetitions,
        interval_s=body.interval_s,
        label=body.label,
    )
    try:
        run = await BenchmarkEngine(spec).run()
    except ProbeError as exc:
        raise _http_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return execution.attach(dict(_store(db, run.to_dict())), "diag.benchmark", target=body.target)


# ---------------------------------------------------------------------------
# Deterministic simulated faults
# ---------------------------------------------------------------------------


@router.post("/faults", status_code=status.HTTP_201_CREATED)
async def diagnostics_faults(body: FaultRequest, db: OptionalDbSession, principal: RequireOperator):
    """
    Run a deterministic simulated fault scenario.

    ``simulated: true`` and ``data_kind: "simulated"`` are always in the
    response. A fixed seed gives a reproducible run; it does not make a real
    network deterministic.
    """
    try:
        scenario = FaultScenario(
            scenario=body.scenario,
            seed=body.seed,
            count=body.count,
            repetitions=body.repetitions,
            timeout_s=body.timeout_s,
            latency_ms=body.latency_ms,
            jitter_ms=body.jitter_ms,
            loss_rate=body.loss_rate,
            period=body.period,
            target=body.target,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    result = run_fault_scenario(scenario)
    return execution.attach(dict(_store(db, result.to_dict())), "diag.fault_scenario",
                            seed=body.seed, inputs=body.model_dump())


# ---------------------------------------------------------------------------
# Harnesses: hysteresis sweep and scheduler comparison
# ---------------------------------------------------------------------------


@router.post("/hysteresis-sweep", status_code=status.HTTP_201_CREATED)
async def diagnostics_hysteresis_sweep(body: HysteresisSweepRequest, principal: RequireOperator):
    """
    Feature F: sweep hysteresis ratios and report the stability/recovery trade-off.

    Ratio 1.0 is always included so the sweep has a no-dead-band reference, and
    the naive single-threshold baseline is reported alongside.
    """
    try:
        result = await hysteresis_sweep(
            ratios=body.ratios,
            f_thresh=body.f_thresh,
            repetitions=body.repetitions,
            seed=body.seed,
            total_intervals=body.total_intervals,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return execution.attach(dict(result), "diag.hysteresis_sweep", seed=body.seed,
                            inputs=body.model_dump())


@router.post("/scheduler-comparison", status_code=status.HTTP_201_CREATED)
async def diagnostics_scheduler_comparison(body: SchedulerComparisonRequest, principal: RequireOperator):
    """
    Feature G: compare scheduler variants over identical, declared path conditions.

    Paths are logical, not independent physical interfaces. The result is a
    controlled benchmark and is labelled as such.
    """
    _enforce_demo_limits(
        payload_bytes=(body.payload_bytes, DEMO_MAX_PAYLOAD_BYTES),
        repetitions=(body.repetitions, DEMO_MAX_REPETITIONS),
        paths=(len(body.paths or []), DEMO_MAX_PATHS),
    )
    if settings.public_demo:
        # The comparison replays modelled delays in real time, so one run holds a
        # worker for tens of seconds. Allow one at a time rather than let anonymous
        # callers stack them.
        if _scheduler_comparison_lock.locked():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="A scheduler comparison is already running on this server. Try again shortly.",
            )
        async with _scheduler_comparison_lock:
            return await _run_scheduler_comparison(body)
    return await _run_scheduler_comparison(body)


async def _run_scheduler_comparison(body: SchedulerComparisonRequest):
    try:
        result = await scheduler_comparison(
            paths=[p.model_dump() for p in body.paths] if body.paths else None,
            payload_bytes=body.payload_bytes,
            chunk_size_bytes=body.chunk_size_bytes,
            repetitions=body.repetitions,
            variants=body.variants,
            retransmit_policies=body.retransmit_policies,
            seed=body.seed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return execution.attach(dict(result), "diag.scheduler_comparison", seed=body.seed,
                            inputs=body.model_dump())


# ---------------------------------------------------------------------------
# History, reports, exports, comparison
# ---------------------------------------------------------------------------


@router.get("/runs")
def diagnostics_runs(principal: RequireRead, limit: int = 50, kind: str | None = None):
    """Newest-first run history summaries."""
    limit = max(1, min(limit, 200))
    return {
        "runs": diagnostics_store.summaries(limit=limit, kind=kind),
        "count": diagnostics_store.count(),
        "max_history": diagnostics_store.max_history,
        "note": "Run history is in-process and is cleared when the API restarts.",
    }


@router.get("/runs/{run_id}")
def diagnostics_run_detail(run_id: str, principal: RequireRead):
    """One stored run, complete."""
    return _require_run(run_id)


@router.get("/runs/{run_id}/report")
def diagnostics_run_report(run_id: str, principal: RequireRead):
    """A readable measurement report for one run."""
    return measurement_report(_require_run(run_id))


@router.get("/runs/{run_id}/export")
def diagnostics_run_export(run_id: str, principal: RequireRead, format: str = "json"):
    """Export one run as JSON (default) or CSV."""
    run = _require_run(run_id)
    if format.lower() == "csv":
        return Response(
            content=run_to_csv(run),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.csv"'},
        )
    if format.lower() in ("json", ""):
        return Response(
            content=run_to_json(run),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'},
        )
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="format must be 'json' or 'csv'.",
    )


@router.post("/runs/compare")
def diagnostics_compare(body: CompareRequest, principal: RequireRead):
    """
    Compare two or more stored runs side by side.

    Deliberately returns no combined score: latency, loss and jitter are
    reported separately, each labelled by whether it was measured or modelled.
    """
    runs = [_require_run(rid) for rid in body.run_ids]
    return compare_runs(runs)


@router.delete("/runs")
def diagnostics_clear(principal: RequireOperator):
    """Empty the in-process run history."""
    removed = diagnostics_store.clear()
    return {"removed": removed, "count": diagnostics_store.count()}


# ---------------------------------------------------------------------------
# Feature A / Feature B integration
# ---------------------------------------------------------------------------


@router.post("/integrations/feature-a/capture", status_code=status.HTTP_201_CREATED)
async def feature_a_capture(body: FeatureABaselineRequest, db: OptionalDbSession, principal: RequireOperator):
    """
    Capture a real baseline measurement to bracket a Feature A experiment.

    Returns a real ping benchmark annotated with the phase. The annotation states
    that Feature A's own link states come from its simulator and are a different
    kind of evidence.
    """
    target = body.target or suggested_target()
    try:
        run = await capture_baseline_measurement(
            phase=body.phase,
            target=target,
            count=body.count,
            timeout_s=body.timeout_s,
            repetitions=body.repetitions,
            label=body.label,
        )
    except ProbeError as exc:
        raise _http_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return _store(db, run)


@router.post("/integrations/feature-a/compare")
def feature_a_compare(body: FeatureACompareRequest, principal: RequireRead):
    """
    Compare the before/after baseline pair.

    The delta is arithmetic on two real measurements; the response is explicit
    that it does not establish that the Feature A experiment caused it.
    """
    before = _require_run(body.before_run_id)
    after = _require_run(body.after_run_id)
    return compare_baseline(before, after)


@router.post("/integrations/feature-b")
def feature_b_pair(body: FeatureBPairRequest, principal: RequireRead):
    """
    Pair a Feature B transfer summary with a diagnostics run, side by side.

    Rejects the pairing with an explicit error if the diagnostics run is not
    stored, rather than fabricating a successful association.
    """
    run = _require_run(body.diagnostics_run_id)
    if not isinstance(body.transfer, dict) or not body.transfer:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A non-empty Feature B transfer summary is required.",
        )
    diagnostics_store.link_transfer(body.diagnostics_run_id, body.transfer)
    return feature_b_side_by_side(run, body.transfer)


@router.get("/integrations/feature-b/{run_id}")
def feature_b_get(run_id: str, principal: RequireRead):
    """Return the stored Feature B pairing for a diagnostics run, if any."""
    run = _require_run(run_id)
    transfer = diagnostics_store.get_transfer(run_id)
    if transfer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No Feature B transfer is associated with run {run_id!r}.",
        )
    return feature_b_side_by_side(run, transfer)
