"""
netrescue/benchmark/report.py

Aggregation and export helpers for comparison runs (PRD §5.4, §6.5, §6.7).

Every comparison is reported with median and p95 over at least five
repetitions, alongside units, workload, environment and the explicit caveat
that all conditions are emulated.
"""

from __future__ import annotations

import csv
import io
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

#: PRD §5.4 / §11 — comparison runs are repeated at least this many times.
MIN_REPETITIONS = 5


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def _percentile(values: Sequence[float], pct: float) -> float | None:
    """
    Nearest-rank percentile.

    p95 over a handful of repetitions should not be interpolated between two
    samples — the nearest actual observation is the honest answer.
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    k = max(0, min(len(ordered) - 1, int(round(pct / 100.0 * (len(ordered) - 1)))))
    return float(ordered[k])


def summarise(values: Iterable[float]) -> dict[str, float | None]:
    """Median / p95 / min / max / mean for one metric across repetitions."""
    vals = [float(v) for v in values]
    if not vals:
        return {"median": None, "p95": None, "min": None, "max": None, "mean": None}
    return {
        "median": round(statistics.median(vals), 4),
        "p95": round(_percentile(vals, 95) or 0.0, 4),
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
        "mean": round(statistics.fmean(vals), 4),
    }


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------


@dataclass
class ComparisonRow:
    """One aggregated row: a (variant, retransmit policy) combination."""

    variant: str
    retransmit_policy: str
    repetitions: int
    duration_s: dict[str, float | None] = field(default_factory=dict)
    goodput_bps: dict[str, float | None] = field(default_factory=dict)
    out_of_order_count: dict[str, float | None] = field(default_factory=dict)
    retransmission_count: dict[str, float | None] = field(default_factory=dict)
    failed_chunks_median: float | None = None
    integrity_ok_count: int = 0
    per_path_utilisation: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "retransmit_policy": self.retransmit_policy,
            "repetitions": self.repetitions,
            "duration_s_median": self.duration_s.get("median"),
            "duration_s_p95": self.duration_s.get("p95"),
            "goodput_bps_median": self.goodput_bps.get("median"),
            "out_of_order_median": self.out_of_order_count.get("median"),
            "out_of_order_p95": self.out_of_order_count.get("p95"),
            "retransmissions_median": self.retransmission_count.get("median"),
            "failed_chunks_median": self.failed_chunks_median,
            "integrity_ok": f"{self.integrity_ok_count}/{self.repetitions}",
            "per_path_utilisation": self.per_path_utilisation,
        }


#: Column order for CSV and console output.
COLUMNS = [
    ("variant", "Variant"),
    ("retransmit_policy", "RTX policy"),
    ("repetitions", "Reps"),
    ("duration_s_median", "Duration s (med)"),
    ("duration_s_p95", "Duration s (p95)"),
    ("goodput_bps_median", "Goodput bps (med)"),
    ("out_of_order_median", "Out-of-order (med)"),
    ("out_of_order_p95", "Out-of-order (p95)"),
    ("retransmissions_median", "Retx (med)"),
    ("failed_chunks_median", "Failed chunks (med)"),
    ("integrity_ok", "SHA-256 ok"),
    ("per_path_utilisation", "Per-path chunks"),
]


def render_table(rows: Sequence[ComparisonRow]) -> str:
    """Render a fixed-width comparison table."""
    header = " | ".join(label for _key, label in COLUMNS)
    sep = "-+-".join("-" * len(label) for _key, label in COLUMNS)
    lines = [header, sep]
    for row in rows:
        data = row.as_dict()
        cells = []
        for key, _label in COLUMNS:
            val = data.get(key)
            if isinstance(val, float):
                val = f"{val:.4f}"
            elif isinstance(val, dict):
                val = ", ".join(f"{k}={v}" for k, v in sorted(val.items())) or "-"
            cells.append("-" if val is None else str(val))
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def to_csv(rows: Sequence[ComparisonRow]) -> str:
    """CSV export of the comparison table (PRD §6.2 CSV export)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([key for key, _label in COLUMNS])
    for row in rows:
        data = row.as_dict()
        writer.writerow([_csv_cell(data.get(key)) for key, _label in COLUMNS])
    return out.getvalue()


def _csv_cell(val: Any) -> str:
    """Render one cell: dicts flatten to k=v; pairs, floats get fixed precision."""
    if val is None:
        return ""
    if isinstance(val, dict):
        return ";".join(f"{k}={v}" for k, v in sorted(val.items()))
    if isinstance(val, float):
        return f"{val:.4f}"
    return str(val)


# ---------------------------------------------------------------------------
# Report envelope
# ---------------------------------------------------------------------------


@dataclass
class ComparisonReport:
    """
    Full result of a comparison harness run.

    Carries the caveats PRD §6.7 requires: units, workload, repetition count,
    environment and limitations.
    """

    run_id: str
    kind: str  # "scheduler" | "retransmit" | "hysteresis-sweep"
    generated_at: datetime
    workload: dict[str, Any]
    environment: dict[str, Any]
    rows: list[ComparisonRow] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=lambda: [
        "All network conditions are emulated via simulation; they are not "
        "measurements of real NIC or switch behaviour.",
        "Results are specific to the recorded environment and workload.",
    ])

    def table(self) -> str:
        return render_table(self.rows)

    def to_csv(self) -> str:
        return to_csv(self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            "generated_at": self.generated_at.isoformat(),
            "workload": self.workload,
            "environment": self.environment,
            "rows": [r.as_dict() for r in self.rows],
            "findings": self.findings,
            "limitations": self.limitations,
        }