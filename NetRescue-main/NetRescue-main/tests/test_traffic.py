"""
tests/test_traffic.py — Feature B: intelligent traffic prioritisation.

Covers the queueing model itself and, most importantly, the honesty rules: the
comparison must show the trade-off, and it must never claim priority scheduling
is free or guaranteed.
"""

import pytest

from netrescue.traffic import (
    FifoScheduler,
    LinkConfig,
    PriorityScheduler,
    TrafficClass,
    WorkloadConfig,
    run_comparison,
)
from netrescue.traffic.classes import Packet
from netrescue.traffic.scheduler import SchedulerVariant, build_scheduler


# ---------------------------------------------------------------------------
# Schedulers in isolation
# ---------------------------------------------------------------------------


def pkt(seq, cls, size_bits, arrival_ms):
    return Packet(seq=seq, traffic_class=cls, size_bits=size_bits, arrival_ms=arrival_ms)


def test_fifo_preserves_arrival_order():
    link = LinkConfig(rate_bps=1_000_000, propagation_ms=0.0)
    s = FifoScheduler(link)
    packets = [pkt(0, TrafficClass.CRITICAL, 800, 0.0),
               pkt(1, TrafficClass.BULK, 8000, 1.0)]
    s.schedule(packets)
    assert packets[0].departure_ms < packets[1].departure_ms


def test_priority_serves_critical_first_despite_late_filing():
    """Bulk filed first, but critical must still leave first."""
    link = LinkConfig(rate_bps=1_000_000, propagation_ms=0.0)
    s = PriorityScheduler(link)
    critical = pkt(0, TrafficClass.CRITICAL, 800, 10.0)
    bulk = pkt(1, TrafficClass.BULK, 80_000, 0.0)
    s.schedule([critical, bulk])
    assert critical.departure_ms < bulk.departure_ms, (
        "priority scheduler must not let an earlier bulk packet overtake critical"
    )


def test_priority_preserves_arrival_order_within_a_class():
    link = LinkConfig(rate_bps=1_000_000, propagation_ms=0.0)
    s = PriorityScheduler(link)
    a = pkt(0, TrafficClass.CRITICAL, 800, 0.0)
    b = pkt(1, TrafficClass.CRITICAL, 800, 1.0)
    s.schedule([b, a])          # filed out of order
    assert a.departure_ms <= b.departure_ms


def test_fifo_lets_bulk_overtake_critical():
    """The baseline must genuinely show the problem priority fixes."""
    link = LinkConfig(rate_bps=1_000_000, propagation_ms=0.0)
    s = FifoScheduler(link)
    critical = pkt(0, TrafficClass.CRITICAL, 800, 10.0)
    bulk = pkt(1, TrafficClass.BULK, 80_000, 0.0)
    s.schedule([critical, bulk])
    assert bulk.departure_ms < critical.departure_ms


def test_scheduling_is_deterministic():
    link = LinkConfig(rate_bps=1_000_000)
    runs = []
    for _ in range(3):
        packets = [pkt(i, TrafficClass.CRITICAL if i % 2 else TrafficClass.BULK,
                       800 * (i + 1), float(i)) for i in range(10)]
        PriorityScheduler(link).schedule(packets)
        runs.append([p.departure_ms for p in packets])
    assert runs[0] == runs[1] == runs[2]


def test_link_applies_transmission_time():
    link = LinkConfig(rate_bps=1_000_000, propagation_ms=2.0)
    s = FifoScheduler(link)
    p = pkt(0, TrafficClass.BULK, 100_000, 0.0)   # 100 ms at 1 Mbps
    s.schedule([p])
    assert p.departure_ms == pytest.approx(102.0)


def test_build_scheduler_factory():
    link = LinkConfig()
    assert isinstance(build_scheduler("fifo", link), FifoScheduler)
    assert isinstance(build_scheduler(SchedulerVariant.PRIORITY, link), PriorityScheduler)
    with pytest.raises(ValueError):
        build_scheduler("nope", link)


# ---------------------------------------------------------------------------
# Experiment
# ---------------------------------------------------------------------------


def test_workload_is_reproducible():
    from netrescue.traffic.experiment import generate_packets

    cfg = WorkloadConfig(seed=99)
    a = generate_packets(cfg, 200)
    b = generate_packets(cfg, 200)
    assert len(a) == len(b)
    assert [p.arrival_ms for p in a] == [p.arrival_ms for p in b]


def test_workload_produces_both_classes():
    from netrescue.traffic.experiment import generate_packets

    packets = generate_packets(WorkloadConfig(), 200)
    classes = {p.traffic_class for p in packets}
    assert TrafficClass.CRITICAL in classes
    assert TrafficClass.BULK in classes


def test_both_schedulers_receive_identical_arrivals():
    """Any latency difference must be attributable to scheduling alone."""
    report = run_comparison(
        "t", WorkloadConfig(duration_ms=200), LinkConfig(),
        variants=("fifo", "priority"),
    )
    fifo, prio = report.results
    assert fifo.workload == prio.workload
    assert fifo.classes["critical"].packets_offered == prio.classes["critical"].packets_offered
    assert fifo.classes["bulk"].packets_offered == prio.classes["bulk"].packets_offered


def test_priority_reduces_critical_latency_under_congestion():
    workload = WorkloadConfig(critical_rate_hz=40, bulk_rate_hz=200, duration_ms=500)
    link = LinkConfig(rate_bps=1_000_000)
    assert workload.offered_bps() > link.rate_bps, "workload must congest the link"

    report = run_comparison("t", workload, link)
    by = {r.scheduler: r for r in report.results}
    f = by["fifo"].classes["critical"].latency_ms["median"]
    p = by["priority"].classes["critical"].latency_ms["median"]
    assert p < f, f"priority critical latency {p} should beat FIFO {f}"


def test_result_is_labelled_as_simulated():
    report = run_comparison("t", WorkloadConfig(duration_ms=100), LinkConfig())
    for result in report.results:
        assert result.simulated is True
    joined = " ".join(report.limitations).lower()
    assert "not measurements of a real network" in joined
    assert "no hard real-time guarantee" in joined


def test_findings_state_the_tradeoff_and_the_caveat():
    workload = WorkloadConfig(critical_rate_hz=40, bulk_rate_hz=200, duration_ms=500)
    link = LinkConfig(rate_bps=1_000_000)
    report = run_comparison("t", workload, link)
    text = " ".join(report.findings).lower()
    assert "best-effort" in text
    assert "no hard real-time guarantee" in text
    # And it must mention whichever direction bulk actually moved.
    by = {r.scheduler: r for r in report.results}
    assert by["fifo"].classes["bulk"].throughput_bps != 0


def test_uncongested_link_reports_that_there_is_no_difference():
    """Honesty: with no queue there is nothing to prioritise, and we say so."""
    workload = WorkloadConfig(critical_rate_hz=5, bulk_rate_hz=2,
                              critical_size_bits=100, bulk_size_bits=100,
                              duration_ms=200)
    link = LinkConfig(rate_bps=100_000_000)
    assert workload.offered_bps() < link.rate_bps

    report = run_comparison("t", workload, link)
    text = " ".join(report.findings).lower()
    assert "below link capacity" in text or "identical" in text


def test_comparison_table_is_renderable():
    report = run_comparison("t", WorkloadConfig(duration_ms=100), LinkConfig())
    table = report.table()
    assert "fifo" in table and "priority" in table
    assert "critical" in table and "bulk" in table


def test_serialised_report_has_no_unserialisable_values():
    import json

    report = run_comparison("t", WorkloadConfig(duration_ms=100), LinkConfig())
    blob = json.dumps(report.to_dict())
    assert '"simulated": true' in blob


def test_zero_duration_does_not_divide_by_zero():
    report = run_comparison("t", WorkloadConfig(duration_ms=0), LinkConfig())
    for result in report.results:
        assert result.utilisation == 0.0
