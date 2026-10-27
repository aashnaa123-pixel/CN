"""
netrescue/diagnostics/faults.py — deterministic simulated fault scenarios.

Feature 3 must demonstrate failure behaviour **without Linux tc/netem**. It does
that by modelling the network in software: a seeded generator decides which
probes are lost, delayed beyond the timeout, or answered slowly, and the module
derives a healthy/unhealthy timeline and the transitions between them.

Everything in this module is labelled ``simulated``. A fixed seed makes a
simulation reproducible; it cannot make a real network deterministic, and the
results say so in ``limitations`` rather than implying otherwise.

Scenarios
---------
    unreachable      every probe fails (black hole / host down)
    latency_jitter   replies, but slowly and inconsistently
    packet_loss      a steady fraction of probes is dropped
    timeout          modelled latency exceeds the probe timeout
    intermittent     alternating reachable/unreachable windows
    healthy_to_unhealthy  starts healthy, degrades, then recovers
    transitions      alias kept for the UI's "healthy/unhealthy transitions"
"""

from __future__ import annotations

import logging
import random
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from netrescue.diagnostics.config import SOFTWARE_VERSION, config_hash
from netrescue.diagnostics.runner import BenchmarkRun, BenchmarkSpec, simulated_benchmark

log = logging.getLogger(__name__)

SCENARIO_NAMES = (
    "unreachable",
    "latency_jitter",
    "packet_loss",
    "timeout",
    "intermittent",
    "healthy_to_unhealthy",
    "transitions",
)


@dataclass
class FaultScenario:
    """A fully specified simulation. Every field participates in the hash."""

    scenario: str = "packet_loss"
    seed: int = 20260101
    count: int = 5           # probes per repetition
    repetitions: int = 3
    timeout_s: float = 1.0
    latency_ms: float = 20.0
    jitter_ms: float = 5.0
    loss_rate: float = 0.2
    #: Window length (in probes) for the intermittent scenario's on/off cycle.
    period: int = 6
    target: str = "simulated-host"

    def __post_init__(self) -> None:
        if self.scenario not in SCENARIO_NAMES:
            raise ValueError(
                f"unknown scenario {self.scenario!r}; expected one of "
                f"{', '.join(SCENARIO_NAMES)}"
            )
        if not 1 <= int(self.count) <= 100:
            raise ValueError("count must be between 1 and 100")
        if not 1 <= int(self.repetitions) <= 50:
            raise ValueError("repetitions must be between 1 and 50")
        if not 0.0 <= self.loss_rate <= 1.0:
            raise ValueError("loss_rate must be between 0.0 and 1.0")

    def _derived(self) -> dict[str, Any]:
        """Map the scenario name onto concrete model parameters."""
        base = {
            "latency_ms": self.latency_ms,
            "jitter_ms": self.jitter_ms,
            "loss_rate": self.loss_rate,
            "unreachable": False,
        }
        if self.scenario == "unreachable":
            base.update(unreachable=True, loss_rate=1.0)
        elif self.scenario == "latency_jitter":
            base.update(latency_ms=max(self.latency_ms, 80.0), jitter_ms=max(self.jitter_ms, 30.0), loss_rate=0.0)
        elif self.scenario == "packet_loss":
            base.update(loss_rate=self.loss_rate if self.loss_rate > 0 else 0.3)
        elif self.scenario == "timeout":
            # Latency well beyond the timeout: nothing can complete in time.
            base.update(latency_ms=max(self.latency_ms, self.timeout_s * 1000.0 * 4.0), jitter_ms=0.0)
        elif self.scenario in ("intermittent", "healthy_to_unhealthy", "transitions"):
            base.update(latency_ms=max(self.latency_ms, 30.0), jitter_ms=max(self.jitter_ms, 10.0))
        return base

    def snapshot(self) -> dict[str, Any]:
        return {
            "kind": "fault_scenario",
            "scenario": self.scenario,
            "seed": self.seed,
            "count_per_repetition": int(self.count),
            "repetitions": int(self.repetitions),
            "timeout_s": self.timeout_s,
            "period": int(self.period),
            "target": self.target,
            **self._derived(),
            "deterministic": True,
            "simulated": True,
            "software_version": SOFTWARE_VERSION,
        }


@dataclass
class ProbeEvent:
    index: int
    ok: bool
    latency_ms: float | None
    state: str  # HEALTHY | UNHEALTHY

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "state": self.state,
        }


@dataclass
class FaultScenarioResult:
    run_id: str
    scenario: str
    seed: int
    config: dict[str, Any]
    config_hash: str
    started_at: float
    timeline: list[ProbeEvent] = field(default_factory=list)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    benchmark: dict[str, Any] | None = None

    @property
    def healthy_count(self) -> int:
        return sum(1 for e in self.timeline if e.ok)

    @property
    def unhealthy_count(self) -> int:
        return len(self.timeline) - self.healthy_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "kind": "fault_scenario",
            "scenario": self.scenario,
            "seed": self.seed,
            "simulated": True,
            "data_kind": "simulated",
            "config": self.config,
            "config_hash": self.config_hash,
            "software_version": SOFTWARE_VERSION,
            "generated_at": self.started_at,
            "probes": [e.as_dict() for e in self.timeline],
            "total_probes": len(self.timeline),
            "healthy_count": self.healthy_count,
            "unhealthy_count": self.unhealthy_count,
            "transitions": self.transitions,
            "transition_count": len(self.transitions),
            "benchmark": self.benchmark,
            "limitations": [
                "SIMULATED: this timeline is produced by a seeded model, not by "
                "probing a real network.",
                "A fixed seed makes this run reproducible; real network "
                "conditions cannot be made deterministic by a seed.",
                "Use the real ping/traceroute/DNS diagnostics for actual "
                "measurements of this machine's network.",
            ],
        }


def _intermittent_unreachable(index: int, period: int) -> bool:
    """First half of each window is down, second half is up."""
    period = max(2, int(period))
    return (index % period) < (period // 2)


def run_fault_scenario(scenario: FaultScenario) -> FaultScenarioResult:
    """
    Execute a deterministic simulated fault scenario.

    Deterministic: identical arguments always produce an identical result and
    an identical ``config_hash``. No network call is made.
    """
    cfg = scenario.snapshot()
    result = FaultScenarioResult(
        run_id=f"sim-{scenario.scenario}-{scenario.seed}",
        scenario=scenario.scenario,
        seed=scenario.seed,
        config=cfg,
        config_hash=config_hash(cfg),
        started_at=time.time(),
    )

    rng = random.Random(scenario.seed)
    derived = scenario._derived()
    latency = float(derived["latency_ms"])
    jitter = float(derived["jitter_ms"])
    base_loss = float(derived["loss_rate"])
    timeout_ms = scenario.timeout_s * 1000.0

    index = 0
    previous_state = "HEALTHY"
    for _rep in range(int(scenario.repetitions)):
        for _ in range(int(scenario.count)):
            if scenario.scenario == "intermittent":
                blocked = _intermittent_unreachable(index, scenario.period)
            elif scenario.scenario == "healthy_to_unhealthy":
                # First third healthy, middle third degraded, last third recovers.
                total = max(1, int(scenario.repetitions) * int(scenario.count))
                third = total / 3.0
                if index < third:
                    blocked = False
                elif index < 2 * third:
                    blocked = rng.random() < 0.85
                else:
                    blocked = rng.random() < 0.15
            else:
                blocked = False

            if derived["unreachable"] or blocked or rng.random() < base_loss:
                event = ProbeEvent(index=index, ok=False, latency_ms=None, state="UNHEALTHY")
            else:
                sample = max(0.0, rng.gauss(latency, max(jitter, 0.0)))
                if sample > timeout_ms:
                    event = ProbeEvent(index=index, ok=False, latency_ms=None, state="UNHEALTHY")
                else:
                    event = ProbeEvent(index=index, ok=True, latency_ms=round(sample, 3), state="HEALTHY")

            if event.state != previous_state:
                result.transitions.append({
                    "index": index,
                    "from": previous_state,
                    "to": event.state,
                    "latency_ms": event.latency_ms,
                })
                previous_state = event.state

            result.timeline.append(event)
            index += 1

    # Reuse the runner's simulation so the aggregate numbers and the timeline
    # come from the same model parameters.
    spec = BenchmarkSpec(
        target=scenario.target,
        count=scenario.count,
        timeout_s=scenario.timeout_s,
        repetitions=scenario.repetitions,
        interval_s=0.0,
        label=scenario.scenario,
    )
    bench: BenchmarkRun = simulated_benchmark(
        spec,
        seed=scenario.seed,
        latency_ms=latency,
        jitter_ms=jitter,
        loss_rate=base_loss,
        unreachable=bool(derived["unreachable"]) or scenario.scenario == "timeout",
    )
    result.benchmark = bench.to_dict()
    log.info(
        "[fault %s] scenario=%s seed=%d probes=%d transitions=%d",
        result.run_id, scenario.scenario, scenario.seed, len(result.timeline), len(result.transitions),
    )
    return result
