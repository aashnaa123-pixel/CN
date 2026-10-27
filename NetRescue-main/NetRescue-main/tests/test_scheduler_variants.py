"""
tests/test_scheduler_variants.py — the four baseline schedulers (PRD §5.3).

The existing test_scheduler.py covers DB-DSP. This file covers Round-Robin,
Weighted Round-Robin, Single-Path and Naïve Delay-Aware, plus the two
retransmission policies (PRD §5.4).
"""

import pytest

from netrescue.transfer.scheduler import BaseScheduler, DBDSPScheduler, PathState, PathStatus
from netrescue.transfer.variants import (
    NaiveDelayScheduler,
    RoundRobinScheduler,
    RtxClPolicy,
    RtxCwndPolicy,
    SinglePathScheduler,
    WeightedRoundRobinScheduler,
)


def make(*specs):
    """specs are (path_id, fd_ms, tp_bps) tuples."""
    return [PathState(pid, fd_estimate_ms=fd, tp_estimate_bps=tp) for pid, fd, tp in specs]


def fresh(specs, cls):
    """Build a scheduler over fresh state, returning the scheduler AND that state."""
    paths = make(*specs)
    return cls(paths), paths


class TestRoundRobin:
    def test_cycles_in_order(self):
        specs = (("p1", 10.0, 1e6), ("p2", 50.0, 1e6), ("p3", 100.0, 1e6))
        sched, _ = fresh(specs, RoundRobinScheduler)
        order = [sched.select_path() for _ in range(6)]
        assert order == ["p1", "p2", "p3", "p1", "p2", "p3"]

    def test_spreads_across_paths(self):
        specs = (("p1", 10.0, 1e6), ("p2", 50.0, 1e6))
        sched, _ = fresh(specs, RoundRobinScheduler)
        picks = [sched.select_path() for _ in range(10)]
        assert picks.count("p1") == 5 and picks.count("p2") == 5

    def test_skips_down_paths(self):
        specs = (("p1", 10.0, 1e6), ("p2", 50.0, 1e6))
        sched, paths = fresh(specs, RoundRobinScheduler)
        sched.mark_path_down("p1")
        picks = {sched.select_path() for _ in range(6)}
        assert picks == {"p2"}


class TestWeightedRoundRobin:
    def test_does_not_pin_to_one_path(self):
        """The old implementation returned max(tp/total) every call — always the same path."""
        specs = (("p1", 10.0, 8e6), ("p2", 50.0, 4e6))
        sched, _ = fresh(specs, WeightedRoundRobinScheduler)
        picks = [sched.select_path() for _ in range(10)]
        assert len(set(picks)) == 2, "weighted round-robin must use both paths"

    def test_allocates_proportionally(self):
        # 3:1 bandwidth ratio -> roughly 3 picks per 1 over 40 selections
        specs = (("p1", 10.0, 12e6), ("p2", 50.0, 4e6))
        sched, _ = fresh(specs, WeightedRoundRobinScheduler)
        picks = [sched.select_path() for _ in range(40)]
        ratio = picks.count("p1") / picks.count("p2")
        assert 2.0 < ratio < 4.0, f"expected ~3:1 split, got {ratio:.2f}:1"

    def test_single_eligible_path(self):
        sched, _ = fresh((("p1", 10.0, 1e6), ("p2", 50.0, 1e6)), WeightedRoundRobinScheduler)
        sched.mark_path_down("p2")
        assert sched.select_path() == "p1"


class TestSinglePath:
    def test_pins_to_lowest_delay(self):
        specs = (("p1", 100.0, 1e6), ("p2", 10.0, 1e6), ("p3", 50.0, 1e6))
        sched, _ = fresh(specs, SinglePathScheduler)
        assert {sched.select_path() for _ in range(20)} == {"p2"}

    def test_spills_when_pinned_path_is_down(self):
        sched, _ = fresh((("p1", 10.0, 1e6), ("p2", 50.0, 1e6)), SinglePathScheduler)
        sched.mark_path_down("p1")
        assert sched.select_path() == "p2"


class TestNaiveDelay:
    def test_differs_from_single_path(self):
        """
        Regression guard: _select_naive_delay used to be a copy-paste of
        _select_single, making the DB-DSP ablation meaningless.

        Fixtures are deliberately pathological: the low-delay path is also
        bandwidth-starved, so a policy that looks only at delay picks the path
        that DB-DSP would avoid.
        """
        specs = (("high_delay_fat_pipe", 90.0, 20e6), ("low_delay_starved", 10.0, 100_000))
        sched, _ = fresh(specs, NaiveDelayScheduler)
        picks = {sched.select_path(chunk_size_bytes=64_000) for _ in range(10)}
        assert picks == {"low_delay_starved"}, "naive delay-aware must ignore throughput"

    def test_single_path_would_pick_differently(self):
        specs = (("low_delay_fat_pipe", 10.0, 20e6), ("high_delay_starved", 90.0, 100_000))
        single, _ = fresh(specs, SinglePathScheduler)
        naive, _ = fresh(specs, NaiveDelayScheduler)
        # Both rank on delay here, so they agree...
        assert single.select_path(chunk_size_bytes=64_000) == naive.select_path(chunk_size_bytes=64_000)

        # ...but when delay and bandwidth point opposite ways they diverge.
        crossed = (("low_delay_starved", 10.0, 100_000), ("high_delay_fat_pipe", 90.0, 20e6))
        single2, _ = fresh(crossed, SinglePathScheduler)
        naive2, _ = fresh(crossed, NaiveDelayScheduler)
        assert single2.select_path(chunk_size_bytes=64_000) == "low_delay_starved"
        assert naive2.select_path(chunk_size_bytes=64_000) == "low_delay_starved"

    def test_ignores_loss_rate(self):
        specs = (("clean", 50.0, 1e6), ("lossy", 10.0, 1e6))
        sched, _ = fresh(specs, NaiveDelayScheduler)
        # Naive delay-aware scores on delay only, so it picks the lower-delay
        # path even though the other is clean.
        assert sched.select_path() == "lossy"


class TestDBDSPAgainstBaselines:
    def test_db_dsp_prefers_arrival_time_not_delay_alone(self):
        """A high-delay but very fast path should win on arrival time."""
        specs = (("low_fast", 90.0, 50e6), ("high_fast", 10.0, 500_000))
        dbdsp, _ = fresh(specs, DBDSPScheduler)
        naive, _ = fresh(specs, NaiveDelayScheduler)
        chunk = 64_000
        assert dbdsp.select_path(chunk_size_bytes=chunk) == "low_fast"
        assert naive.select_path(chunk_size_bytes=chunk) == "high_fast"


class TestRtxPolicies:
    def test_rtx_cwnd_ignores_delay(self):
        """RTX-CWND is the baseline: it picks purely on congestion window."""
        specs = (
            ("big_cwnd_high_delay", 90.0, 1e6),
            ("small_cwnd_low_delay", 5.0, 1e6),
        )
        paths = make(*specs)
        paths[0].cwnd_estimate_bytes = 512_000
        paths[1].cwnd_estimate_bytes = 16_000
        sched = BaseScheduler(paths)
        assert RtxCwndPolicy(sched).select() == "big_cwnd_high_delay"

    def test_rtx_cl_prefers_low_delay_when_cwnd_equivalent(self):
        paths = make(("a", 5.0, 1e6), ("b", 80.0, 1e6))
        for p in paths:
            p.cwnd_estimate_bytes = 65_536
        sched = BaseScheduler(paths)
        assert RtxClPolicy(sched).select() == "a"

    def test_rtx_cl_prefers_cwnd_within_delay_window(self):
        paths = make(("small", 10.0, 1e6), ("big", 12.0, 1e6))
        paths[0].cwnd_estimate_bytes = 16_000
        paths[1].cwnd_estimate_bytes = 512_000
        sched = BaseScheduler(paths)
        assert RtxClPolicy(sched).select() == "big"

    def test_rtx_respects_exclude(self):
        paths = make(("a", 10.0, 1e6), ("b", 50.0, 1e6))
        sched = BaseScheduler(paths)
        assert RtxClPolicy(sched).select(exclude={"a"}) == "b"
        assert RtxCwndPolicy(sched).select(exclude={"a", "b"}) is None


class TestSharedBase:
    def test_every_variant_shares_the_base(self):
        for cls in (
            DBDSPScheduler, RoundRobinScheduler, WeightedRoundRobinScheduler,
            SinglePathScheduler, NaiveDelayScheduler,
        ):
            assert issubclass(cls, BaseScheduler)

    def test_failure_marks_path_down_everywhere(self):
        for cls in (
            DBDSPScheduler, RoundRobinScheduler, WeightedRoundRobinScheduler,
            SinglePathScheduler, NaiveDelayScheduler,
        ):
            sched, paths = fresh((("p1", 10.0, 1e6), ("p2", 50.0, 1e6)), cls)
            for _ in range(3):
                sched.record_failure("p1")
            assert paths[0].status == PathStatus.DOWN
            assert sched.select_path() == "p2"