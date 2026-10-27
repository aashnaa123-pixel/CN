"""
netrescue/traffic — Feature B: intelligent traffic prioritisation.

Two traffic classes sharing one constrained link:

  CRITICAL  latency-sensitive control/heartbeat traffic — small, must arrive soon
  BULK      file/transfer traffic — large, tolerant of delay

Two schedulers decide what leaves the link and when:

  FIFO         baseline; arrival order, no class awareness
  PRIORITY     critical first, bulk only fills otherwise-idle capacity

The point of the feature is the *trade-off*: prioritising critical traffic
lowers its latency but can starve bulk, so throughput for bulk falls. Neither
scheduler makes bulk faster, and neither offers a hard real-time guarantee —
PRIORITY is best-effort. Both results are reported side by side so the trade-off
is visible rather than asserted.

This is a deterministic queueing model, not a measurement of a real network.
Every report says so. For real network conditions use netrescue/benchmark.
"""

from netrescue.traffic.classes import TrafficClass, CLASS_PRIORITY, Packet
from netrescue.traffic.experiment import (
    ExperimentResult,
    LinkConfig,
    SchedulerVariant,
    TrafficExperiment,
    WorkloadConfig,
    run_comparison,
)
from netrescue.traffic.scheduler import (
    BaseLinkScheduler,
    FifoScheduler,
    PriorityScheduler,
    TokenBucketLink,
    build_scheduler,
)

__all__ = [
    "TrafficClass",
    "CLASS_PRIORITY",
    "Packet",
    "FifoScheduler",
    "PriorityScheduler",
    "BaseLinkScheduler",
    "TokenBucketLink",
    "build_scheduler",
    "LinkConfig",
    "WorkloadConfig",
    "SchedulerVariant",
    "ExperimentResult",
    "TrafficExperiment",
    "run_comparison",
]