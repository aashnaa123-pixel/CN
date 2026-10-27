"""
netrescue/diagnostics/report.py — exports, comparison and the measurement report.

Runs are stored as plain dictionaries (the shape ``BenchmarkRun.to_dict`` and
``FaultScenarioResult.to_dict`` produce). Everything here works on that shape so
the API, the exports and the UI all describe a run the same way.

Reporting rules this module enforces:

  * A result is always labelled ``data_kind``: ``real`` (measured on this
    machine's network) or ``simulated`` (produced by a seeded model).
  * No combined score. Latency, loss, jitter and duration are reported
    separately with their units, because collapsing them into one number would
    hide exactly the trade-offs the feature exists to expose.
  * Every report carries its configuration hash, environment and limitations.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Iterable, Sequence

from netrescue.diagnostics.config import SOFTWARE_VERSION


def run_data_kind(run: dict[str, Any]) -> str:
    """A run is ``simulated`` if it says so, otherwise it was really measured."""
    return "simulated" if run.get("simulated") else "real"


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


def run_to_json(run: dict[str, Any]) -> str:
    payload = {
        "export_format": "netrescue.feature3.diagnostics.run",
        "export_version": 1,
        "software_version": SOFTWARE_VERSION,
        "data_kind": run_data_kind(run),
        "run": run,
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def run_to_csv(run: dict[str, Any]) -> str:
    """
    Flatten a run into per-repetition rows plus an aggregate row.

    Columns are stable so a spreadsheet, a diff and a later import all agree.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "run_id", "kind", "data_kind", "target", "scenario", "repetition", "ok",
        "sent", "received", "lost", "loss_pct", "min_ms", "avg_ms", "max_ms",
        "stddev_ms", "duration_s", "error", "config_hash", "software_version",
    ])

    config = run.get("config") or {}
    target = config.get("target", "")
    scenario = config.get("scenario", "")
    config_hash = run.get("config_hash", "")
    kind = run.get("kind", "")
    data_kind = run_data_kind(run)

    repetitions = run.get("repetitions")
    if repetitions is None and run.get("benchmark"):
        # Fault scenario: export its embedded benchmark repetitions.
        repetitions = (run.get("benchmark") or {}).get("repetitions")

    aggregate = run.get("aggregate") or (run.get("benchmark") or {}).get("aggregate") or {}

    for rep in repetitions or []:
        writer.writerow([
            run.get("run_id", ""), kind, data_kind, target, scenario,
            rep.get("index", ""), int(bool(rep.get("ok"))),
            rep.get("sent", ""), rep.get("received", ""), rep.get("lost", ""),
            _pct(rep.get("loss_rate")),
            _num(rep.get("min_ms")), _num(rep.get("avg_ms")),
            _num(rep.get("max_ms")), _num(rep.get("stddev_ms")),
            _num(rep.get("duration_s")), rep.get("error") or "",
            config_hash, SOFTWARE_VERSION,
        ])

    if aggregate:
        writer.writerow([
            run.get("run_id", ""), kind, data_kind, target, scenario,
            "AGGREGATE", int((run.get("status") or (run.get("benchmark") or {}).get("status")) == "DONE"),
            aggregate.get("sent", ""), aggregate.get("received", ""), aggregate.get("lost", ""),
            _pct(aggregate.get("packet_loss_rate")),
            _num(aggregate.get("min_ms")), _num(aggregate.get("avg_ms")),
            _num(aggregate.get("max_ms")), _num(aggregate.get("stddev_ms")),
            "", "", config_hash, SOFTWARE_VERSION,
        ])
    return buf.getvalue()


def runs_to_csv(runs: Sequence[dict[str, Any]]) -> str:
    """One CSV holding several runs, with a single header row."""
    buf = io.StringIO()
    header_written = False
    for run in runs:
        block = run_to_csv(run)
        lines = block.splitlines()
        if not lines:
            continue
        if header_written:
            lines = lines[1:]
        writer = buf
        writer.write("\n".join(lines))
        writer.write("\n")
        header_written = True
    return buf.getvalue()


def _num(value: Any) -> Any:
    if value is None:
        return ""
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return value


def _pct(fraction: Any) -> str:
    if fraction is None:
        return ""
    try:
        return f"{float(fraction) * 100:.2f}"
    except (TypeError, ValueError):
        return str(fraction)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def compare_runs(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """
    Side-by-side comparison of two or more runs.

    Returns one labelled row per run. There is intentionally **no** combined or
    normalised score: latency, loss and jitter are three different quantities
    and a single index would invite a ranking the data cannot support.
    """
    rows = []
    for run in runs:
        aggregate = run.get("aggregate") or (run.get("benchmark") or {}).get("aggregate") or {}
        config = run.get("config") or {}
        rows.append({
            "run_id": run.get("run_id"),
            "data_kind": run_data_kind(run),
            "kind": run.get("kind", ""),
            "scenario": config.get("scenario"),
            "target": config.get("target"),
            "status": run.get("status") or (run.get("benchmark") or {}).get("status"),
            "config_hash": run.get("config_hash"),
            "software_version": run.get("software_version", SOFTWARE_VERSION),
            "repetitions": run.get("repetitions")
            if isinstance(run.get("repetitions"), int)
            else len(run.get("repetitions") or []),
            "sent": aggregate.get("sent"),
            "received": aggregate.get("received"),
            "packet_loss_pct": (
                f"{aggregate['packet_loss_rate'] * 100:.2f}"
                if aggregate.get("packet_loss_rate") is not None else None
            ),
            "min_ms": aggregate.get("min_ms"),
            "avg_ms": aggregate.get("avg_ms"),
            "max_ms": aggregate.get("max_ms"),
            "stddev_ms": aggregate.get("stddev_ms"),
            "median_ms": aggregate.get("median_ms"),
        })

    # Honest warnings about mixing kinds of result in one table.
    mixed = len({r["data_kind"] for r in rows}) > 1
    same_hash = len({r["config_hash"] for r in rows}) == 1
    notes = [
        "Metrics are labelled by data_kind: 'real' was measured on this "
        "machine's network, 'simulated' came from a seeded model.",
        "There is no combined score by design — latency, loss and jitter are "
        "not interchangeable.",
    ]
    if mixed:
        notes.append(
            "This comparison mixes real and simulated runs; read the data_kind "
            "column before drawing any conclusion."
        )
    if same_hash and len(rows) > 1:
        notes.append(
            "All runs share one configuration hash, so they were set up "
            "identically — differences come from the environment or the model, "
            "not from configuration drift."
        )
    return {
        "runs": rows,
        "run_count": len(rows),
        "data_kinds": sorted({r["data_kind"] for r in rows}),
        "notes": notes,
        "generated_at_utc": _utc_now(),
    }


# ---------------------------------------------------------------------------
# Measurement report
# ---------------------------------------------------------------------------


def measurement_report(run: dict[str, Any]) -> dict[str, Any]:
    """
    A single run turned into a readable, self-describing report.

    The report is deliberately verbose about provenance because that is the part
    a reader is most likely to get wrong: which numbers were measured, which
    were modelled, and on what machine.
    """
    kind = run_data_kind(run)
    config = run.get("config") or {}
    environment = run.get("environment") or {}
    aggregate = run.get("aggregate") or (run.get("benchmark") or {}).get("aggregate") or {}
    status = run.get("status") or (run.get("benchmark") or {}).get("status")

    findings: list[str] = []
    if aggregate.get("sent"):
        findings.append(
            f"{aggregate.get('received', 0)}/{aggregate.get('sent')} probes were "
            f"answered ({_pct(aggregate.get('packet_loss_rate'))}% lost)."
        )
    if aggregate.get("avg_ms") is not None:
        findings.append(
            f"Round-trip time averaged {aggregate['avg_ms']} ms "
            f"(min {aggregate.get('min_ms')}, max {aggregate.get('max_ms')}, "
            f"stddev {aggregate.get('stddev_ms')})."
        )
    if run.get("kind") == "fault_scenario":
        findings.append(
            f"The simulated timeline moved between states "
            f"{run.get('transition_count', 0)} time(s) across "
            f"{run.get('total_probes', 0)} probes."
        )
    if not findings:
        findings.append("The run produced no usable samples; see errors and limitations.")

    return {
        "report_format": "netrescue.feature3.measurement_report",
        "report_version": 1,
        "run_id": run.get("run_id"),
        "data_kind": kind,
        "status": status,
        "configuration": config,
        "config_hash": run.get("config_hash"),
        "software_version": run.get("software_version", SOFTWARE_VERSION),
        "environment": environment,
        "results": {
            "sent": aggregate.get("sent"),
            "received": aggregate.get("received"),
            "lost": aggregate.get("lost"),
            "packet_loss_pct": (
                f"{aggregate['packet_loss_rate'] * 100:.2f}%"
                if aggregate.get("packet_loss_rate") is not None else None
            ),
            "min_ms": aggregate.get("min_ms"),
            "avg_ms": aggregate.get("avg_ms"),
            "max_ms": aggregate.get("max_ms"),
            "stddev_ms": aggregate.get("stddev_ms"),
            "median_ms": aggregate.get("median_ms"),
            "units": {"latency": "ms", "loss": "percent"},
        },
        "findings": findings,
        "errors": run.get("errors") or [],
        "limitations": run.get("limitations") or [],
        "generated_at_utc": _utc_now(),
    }


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def runs_limitations(runs: Iterable[dict[str, Any]]) -> list[str]:
    """Union of the limitations across several runs, de-duplicated, ordered."""
    seen: list[str] = []
    for run in runs:
        for item in run.get("limitations") or []:
            if item not in seen:
                seen.append(item)
    return seen
