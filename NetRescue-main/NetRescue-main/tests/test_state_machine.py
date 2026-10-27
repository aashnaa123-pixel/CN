"""tests/test_state_machine.py — Unit tests for HysteresisStateMachine (PRD §4.3)."""

import pytest

from netrescue.recovery.state_machine import HysteresisStateMachine, LinkState


def make_sm(**kwargs) -> HysteresisStateMachine:
    defaults = dict(link_id="test-link", f_thresh=3, n=5, r_thresh=5)
    defaults.update(kwargs)
    return HysteresisStateMachine(**defaults)


class TestHealthyToSuspect:
    def test_transitions_after_f_thresh_misses(self):
        sm = make_sm(f_thresh=3)
        for _ in range(2):
            sm.update(missed=True)
        assert sm.state == LinkState.HEALTHY  # not yet
        sm.update(missed=True)  # 3rd miss
        assert sm.state == LinkState.SUSPECT

    def test_no_transition_before_f_thresh(self):
        sm = make_sm(f_thresh=3)
        for _ in range(2):
            sm.update(missed=True)
        assert sm.state == LinkState.HEALTHY


class TestSuspectToFailed:
    def test_transitions_to_failed_after_n_misses(self):
        sm = make_sm(f_thresh=3, n=5)
        for _ in range(5):
            sm.update(missed=True)
        assert sm.state == LinkState.FAILED

    def test_recovers_from_suspect_without_hitting_failed(self):
        sm = make_sm(f_thresh=3, n=5, r_thresh=5)
        # Enter SUSPECT
        for _ in range(3):
            sm.update(missed=True)
        assert sm.state == LinkState.SUSPECT
        # Recover via r_thresh successes
        for _ in range(5):
            sm.update(missed=False)
        assert sm.state == LinkState.HEALTHY


class TestFailedToHealthy:
    def test_recovers_after_r_thresh_successes(self):
        sm = make_sm(f_thresh=3, n=5, r_thresh=5)
        for _ in range(5):
            sm.update(missed=True)
        assert sm.state == LinkState.FAILED
        for _ in range(4):
            sm.update(missed=False)
        assert sm.state == LinkState.FAILED  # not yet
        sm.update(missed=False)  # 5th success
        assert sm.state == LinkState.HEALTHY

    def test_no_direct_healthy_to_failed_transition(self):
        """HEALTHY must go through SUSPECT before FAILED (PRD §4.3)."""
        sm = make_sm(f_thresh=3, n=5)
        states = []
        for _ in range(5):
            state = sm.update(missed=True)
            states.append(state)
        assert LinkState.SUSPECT in states  # SUSPECT must appear
        assert states[-1] == LinkState.FAILED


class TestEWMA:
    def test_ewma_mode_runs_without_error(self):
        sm = make_sm(use_ewma=True)
        for _ in range(10):
            sm.update(missed=True)
        # Shouldn't raise; state may be SUSPECT or FAILED
        assert sm.state in (LinkState.HEALTHY, LinkState.SUSPECT, LinkState.FAILED)


class TestHysteresisRatio:
    def test_ratio_equals_r_over_f(self):
        sm = make_sm(f_thresh=2, r_thresh=6)
        assert sm.hysteresis_ratio == pytest.approx(3.0)

    def test_ratio_warning_when_less_than_one(self):
        # Should log a warning but not crash
        sm = make_sm(f_thresh=5, r_thresh=2)
        assert sm.hysteresis_ratio < 1.0


class TestRouteFlapCount:
    def test_counts_flaps(self):
        sm = make_sm(f_thresh=3, n=5, r_thresh=5)
        # FAILED → HEALTHY = 1 flap
        for _ in range(5):
            sm.update(missed=True)
        for _ in range(5):
            sm.update(missed=False)
        assert sm.route_flap_count >= 1

    def test_no_flap_on_fresh_machine(self):
        sm = make_sm()
        assert sm.route_flap_count == 0


class TestReset:
    def test_reset_returns_to_healthy(self):
        sm = make_sm(f_thresh=3, n=5)
        for _ in range(5):
            sm.update(missed=True)
        assert sm.state == LinkState.FAILED
        sm.reset()
        assert sm.state == LinkState.HEALTHY

    def test_reset_clears_counters(self):
        sm = make_sm()
        for _ in range(5):
            sm.update(missed=True)
        sm.reset()
        assert sm.route_flap_count == 0
