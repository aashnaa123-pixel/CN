"""
tests/test_hysteresis_sweep.py — the naive baseline and the sweep harness.

Covers PRD §4.2 (naive detector), §4.4 and §6.4 (sweep + Pareto frontier).
"""

import pytest

from netrescue.benchmark.sweep import (
    HysteresisSweepHarness,
    SweepConfig,
    build_outcome_sequence,
    compute_pareto_frontier,
)
from netrescue.recovery.naive import NaiveThresholdDetector
from netrescue.recovery.state_machine import HysteresisStateMachine, LinkState


def state_name(detector):
    s = detector.state
    return s.value if isinstance(s, LinkState) else str(s)


class TestNaiveDetector:
    def test_single_threshold_two_states(self):
        d = NaiveThresholdDetector("l", threshold=3)
        assert d.update(False) == "HEALTHY"
        assert d.update(True) == "HEALTHY"     # 1 miss < 3
        assert d.update(True) == "HEALTHY"     # 2 misses
        assert d.update(True) == "FAILED"      # 3 misses

    def test_flaps_on_oscillation(self):
        # A naïve detector needs *bursts* long enough to cross its threshold:
        # alternating miss/success never accumulates consecutive misses.
        d = NaiveThresholdDetector("l", threshold=2)
        for _ in range(3):
            d.update(True)     # crosses into FAILED
            d.update(True)
            d.update(False)    # back to HEALTHY
            d.update(False)
        assert d.route_flap_count == 6, "every toggle is a route change"
        assert d.transition_count == 6

    def test_requires_consecutive_successes_to_recover(self):
        d = NaiveThresholdDetector("l", threshold=2)
        d.update(True); d.update(True)
        assert state_name(d) == "FAILED"
        d.update(False)
        assert state_name(d) == "FAILED"  # 1 success < 2
        d.update(False)
        assert state_name(d) == "HEALTHY"


class TestFlapParity:
    """Both detectors must count flaps the same way, or the comparison is invalid."""

    def test_hysteresis_flaps_only_on_failed_edges(self):
        sm = HysteresisStateMachine("l", f_thresh=2, n=3, r_thresh=2)
        sm.update(False)
        assert sm.route_flap_count == 0

        # HEALTHY -> SUSPECT: the link is still forwarding, so no route change.
        sm.update(True)
        sm.update(True)
        assert sm.state == LinkState.SUSPECT
        assert sm.route_flap_count == 0

        # SUSPECT -> FAILED: the route must actually change. That is flap 1.
        sm.update(True)
        assert sm.state == LinkState.FAILED
        assert sm.route_flap_count == 1

        # FAILED -> HEALTHY: back to the primary. Flap 2.
        sm.update(False); sm.update(False)
        assert sm.state == LinkState.HEALTHY
        assert sm.route_flap_count == 2

    def test_hysteresis_flaps_less_than_naive_under_oscillation(self):
        # Bursts long enough to cross a threshold, alternating loss and recovery.
        seq = [True, True, False, False] * 12
        sm = HysteresisStateMachine("l", f_thresh=4, n=6, r_thresh=8)
        nv = NaiveThresholdDetector("l", threshold=2)
        for missed in seq:
            sm.update(missed)
            nv.update(missed)
        assert nv.route_flap_count > 0, "the naive baseline must actually flap here"
        assert sm.route_flap_count < nv.route_flap_count

    def test_no_direct_healthy_failed_transition(self):
        """PRD §4.3 — every transition to FAILED passes through SUSPECT."""
        sm = HysteresisStateMachine("l", f_thresh=1, n=2, r_thresh=5)
        seen = []
        sm.on_transition = lambda e: seen.append((e.from_state, e.to_state))
        sm.update(True)
        sm.update(True)
        assert ("HEALTHY", "FAILED") not in seen
        assert ("HEALTHY", "SUSPECT") in seen


class TestOutcomeSequence:
    def test_outage_is_sustained(self):
        cfg = SweepConfig(run_id="t", total_intervals=100, outage_start=10, outage_length=20)
        seq = build_outcome_sequence(cfg, seed=1)
        assert len(seq) == 100
        assert all(seq[10:30]), "outage window must be continuously missed"

    def test_deterministic_for_a_seed(self):
        cfg = SweepConfig(run_id="t")
        assert build_outcome_sequence(cfg, seed=7) == build_outcome_sequence(cfg, seed=7)
        assert build_outcome_sequence(cfg, seed=7) != build_outcome_sequence(cfg, seed=8)


class TestPareto:
    @staticmethod
    def point(ratio, flaps, recovery_s):
        from netrescue.benchmark.sweep import SweepPoint
        return SweepPoint(
            ratio=ratio, f_thresh=3, r_thresh=int(3 * ratio), n=int(3 * ratio),
            flap_count=flaps, recovery_intervals=recovery_s,
            recovery_s=recovery_s, detection_intervals=1, detection_s=1.0,
        )

    def test_dominated_points_are_removed(self):
        # A genuine trade-off: ratio 1.0 recovers fastest but flaps most,
        # ratio 2.0 flaps least but recovers slower. 1.5 is worse than 2.0 on
        # both axes, so it must drop out.
        points = [
            self.point(1.0, 20, 1.0),
            self.point(1.5, 5, 5.0),   # dominated by 2.0
            self.point(2.0, 2, 2.0),
        ]
        ratios = {p.ratio for p in compute_pareto_frontier(points)}
        assert ratios == {1.0, 2.0}

    def test_point_worse_on_both_axes_is_dominated(self):
        points = [
            self.point(1.0, 1, 1.0),   # best on both
            self.point(2.0, 5, 5.0),   # worse on both
        ]
        ratios = {p.ratio for p in compute_pareto_frontier(points)}
        assert ratios == {1.0}

    def test_tradeoff_keeps_both_ends(self):
        # Faster recovery costs more flaps; both ends are non-dominated.
        points = [
            self.point(1.0, 20, 1.0),
            self.point(2.0, 10, 3.0),
            self.point(3.0, 1, 8.0),
        ]
        ratios = {p.ratio for p in compute_pareto_frontier(points)}
        assert ratios == {1.0, 2.0, 3.0}

    def test_points_without_recovery_are_skipped(self):
        from netrescue.benchmark.sweep import SweepPoint
        broken = SweepPoint(
            ratio=9.0, f_thresh=3, r_thresh=9, n=9, flap_count=0,
            recovery_intervals=None, recovery_s=None,
            detection_intervals=None, detection_s=None,
        )
        assert compute_pareto_frontier([broken]) == []


class TestSweepHarness:
    @pytest.mark.asyncio
    async def test_produces_a_point_per_ratio(self):
        cfg = SweepConfig(
            run_id="sweep-test", ratios=[1.0, 2.0], repetitions=2,
            total_intervals=200, outage_start=40, outage_length=40,
            flap_probability=0.25,
        )
        report = await HysteresisSweepHarness(cfg).run()

        assert len(report.points) == 2
        assert [p.ratio for p in report.points] == [1.0, 2.0]
        assert report.naive_flap_count > 0
        assert report.findings
        # r_thresh must be derived from the ratio so it is the only variable.
        assert report.points[1].r_thresh == 2 * cfg.f_thresh

    @pytest.mark.asyncio
    async def test_report_serialises(self):
        cfg = SweepConfig(run_id="sweep-json", ratios=[1.5], repetitions=2, total_intervals=150)
        report = await HysteresisSweepHarness(cfg).run()
        data = report.to_dict()
        assert data["run_id"] == "sweep-json"
        assert "pareto_frontier" in data
        assert "limitations" in data and data["limitations"]
        assert isinstance(report.table(), str)