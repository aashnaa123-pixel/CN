"""
netrescue.benchmark — Feature D: benchmarking and the comparison harnesses (PRD §6).

  runner    : workload generator measuring RTT, throughput, loss and jitter
  sweep     : hysteresis ratio sweep + Pareto frontier (PRD §6.4)
  comparison: scheduler and retransmission comparison (PRD §6.5)
  report    : median/p95 aggregation and CSV/table export (PRD §6.7)
"""

from netrescue.benchmark.comparison import (
    ALL_RETRANSMIT_POLICIES,
    ALL_VARIANTS,
    ComparisonConfig,
    PathSpec,
    SchedulerComparisonHarness,
    run_scheduler_comparison,
)
from netrescue.benchmark.report import (
    MIN_REPETITIONS,
    ComparisonReport,
    ComparisonRow,
    render_table,
    summarise,
    to_csv,
)
from netrescue.benchmark.runner import (
    BenchmarkConfig,
    BenchmarkResults,
    BenchmarkRunner,
)
from netrescue.benchmark.sweep import (
    DEFAULT_RATIOS,
    HysteresisSweepHarness,
    ParetoPoint,
    SweepConfig,
    SweepReport,
    SweepPoint,
    run_hysteresis_sweep,
)

__all__ = [
    # runner
    "BenchmarkConfig",
    "BenchmarkResults",
    "BenchmarkRunner",
    # sweep
    "HysteresisSweepHarness",
    "SweepConfig",
    "SweepReport",
    "SweepPoint",
    "ParetoPoint",
    "DEFAULT_RATIOS",
    "run_hysteresis_sweep",
    # comparison
    "SchedulerComparisonHarness",
    "ComparisonConfig",
    "PathSpec",
    "ALL_VARIANTS",
    "ALL_RETRANSMIT_POLICIES",
    "run_scheduler_comparison",
    # report
    "ComparisonReport",
    "ComparisonRow",
    "MIN_REPETITIONS",
    "summarise",
    "render_table",
    "to_csv",
]