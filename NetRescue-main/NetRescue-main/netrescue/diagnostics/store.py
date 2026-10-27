"""
netrescue/diagnostics/store.py — run history for Feature 3.

Two layers, in this order:

  1. An in-process history, always available, bounded in size. This is what the
     dashboard reads and what the exports serialise. Like the rest of the live
     labs, it is cleared by a server restart — that limitation is stated to the
     caller rather than hidden.
  2. Best-effort persistence into the existing ``experiments`` /
     ``metric_samples`` tables when a database session is present. No new table
     and no new schema: a diagnostics run is an Experiment with ``feature="D"``,
     its configuration snapshot in ``config`` and its headline metrics as
     MetricSample rows. If the database is down the run is still recorded
     in-process and the response says it was not persisted.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

log = logging.getLogger(__name__)

#: How many runs the in-process history keeps. Older runs are dropped; the
#: response always reports the true stored count so the cap is visible.
MAX_HISTORY = 200


class DiagnosticsStore:
    """Thread-safe, bounded, in-process history of diagnostics runs."""

    def __init__(self, max_history: int = MAX_HISTORY):
        self._lock = threading.Lock()
        self._max = max_history
        self._order: list[str] = []
        self._runs: dict[str, dict[str, Any]] = {}
        #: run_id -> transfer summary, for the Feature B association.
        self._transfer_links: dict[str, dict[str, Any]] = {}

    # -- writes -------------------------------------------------------------

    def record(self, run: dict[str, Any]) -> dict[str, Any]:
        run_id = run.get("run_id")
        if not run_id:
            raise ValueError("a run must have a run_id")
        with self._lock:
            if run_id in self._runs:
                self._runs[run_id] = run
            else:
                self._order.append(run_id)
                self._runs[run_id] = run
            while len(self._order) > self._max:
                evicted = self._order.pop(0)
                self._runs.pop(evicted, None)
                self._transfer_links.pop(evicted, None)
        return run

    def link_transfer(self, run_id: str, transfer: dict[str, Any]) -> None:
        with self._lock:
            if run_id in self._runs:
                self._transfer_links[run_id] = transfer

    # -- reads --------------------------------------------------------------

    def get(self, run_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return self._runs.get(run_id)

    def get_transfer(self, run_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return self._transfer_links.get(run_id)

    def list(self, *, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        """Newest first, optionally filtered by ``kind``."""
        with self._lock:
            ids = list(reversed(self._order))
            runs = [self._runs[i] for i in ids if kind is None or self._runs[i].get("kind") == kind]
        return runs[: max(0, limit)]

    def summaries(self, *, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        """Compact rows for the history table — no raw probe arrays."""
        out = []
        for run in self.list(limit=limit, kind=kind):
            aggregate = run.get("aggregate") or (run.get("benchmark") or {}).get("aggregate") or {}
            config = run.get("config") or {}
            out.append({
                "run_id": run.get("run_id"),
                "kind": run.get("kind"),
                "scenario": config.get("scenario"),
                "target": config.get("target"),
                "status": run.get("status") or (run.get("benchmark") or {}).get("status"),
                "simulated": bool(run.get("simulated")),
                "data_kind": "simulated" if run.get("simulated") else "real",
                "config_hash": run.get("config_hash"),
                "generated_at": run.get("generated_at")
                or run.get("started_at"),
                "sent": aggregate.get("sent"),
                "received": aggregate.get("received"),
                "lost": aggregate.get("lost"),
                "packet_loss_pct": (
                    f"{aggregate['packet_loss_rate'] * 100:.2f}"
                    if aggregate.get("packet_loss_rate") is not None else None
                ),
                "avg_ms": aggregate.get("avg_ms"),
                "min_ms": aggregate.get("min_ms"),
                "max_ms": aggregate.get("max_ms"),
                "stddev_ms": aggregate.get("stddev_ms"),
                "persisted": bool(run.get("persisted")),
            })
        return out

    def count(self) -> int:
        with self._lock:
            return len(self._order)

    @property
    def max_history(self) -> int:
        return self._max

    def clear(self) -> int:
        with self._lock:
            n = len(self._order)
            self._order.clear()
            self._runs.clear()
            self._transfer_links.clear()
        return n


#: One store per process, mirroring the other in-process labs.
diagnostics_store = DiagnosticsStore()


# ---------------------------------------------------------------------------
# Best-effort persistence into the existing schema
# ---------------------------------------------------------------------------


def persist_run(session: Any, run: dict[str, Any]) -> bool:
    """
    Write a run into ``experiments`` + ``metric_samples``.

    Returns True when it was written, False when the session is unusable. Never
    raises: a diagnostics run must still be returned to the user if the
    database is unavailable, and the caller marks ``persisted`` accordingly.
    """
    if session is None:
        return False
    try:
        from netrescue.core.models import Experiment, MetricSample

        config = run.get("config") or {}
        aggregate = run.get("aggregate") or (run.get("benchmark") or {}).get("aggregate") or {}

        experiment = Experiment(
            id=run.get("run_id"),
            feature="D",
            config={
                "kind": "diagnostics",
                "config": config,
                "config_hash": run.get("config_hash"),
                "simulated": bool(run.get("simulated")),
                "data_kind": "simulated" if run.get("simulated") else "real",
                "aggregate": aggregate,
                "scenario": config.get("scenario"),
                "target": config.get("target"),
            },
            code_version=run.get("software_version"),
            status="DONE" if (run.get("status") or (run.get("benchmark") or {}).get("status")) in (None, "DONE", "PARTIAL") else "FAILED",
            environment=run.get("environment") or {},
        )
        session.merge(experiment)

        for name, value, unit in (
            ("packet_loss_rate", aggregate.get("packet_loss_rate"), "fraction"),
            ("latency_avg", aggregate.get("avg_ms"), "ms"),
            ("latency_min", aggregate.get("min_ms"), "ms"),
            ("latency_max", aggregate.get("max_ms"), "ms"),
            ("latency_stddev", aggregate.get("stddev_ms"), "ms"),
        ):
            if value is None:
                continue
            session.add(MetricSample(
                run_id=experiment.id,
                name=name,
                value=float(value),
                unit=unit,
                labels={"source": run.get("source", "diagnostics")},
            ))
        session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - persistence is optional
        log.warning("diagnostics run %s not persisted: %s", run.get("run_id"), exc)
        try:
            session.rollback()
        except Exception:  # pragma: no cover - defensive
            pass
        return False
