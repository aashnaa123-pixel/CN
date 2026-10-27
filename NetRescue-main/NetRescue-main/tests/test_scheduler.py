"""tests/test_scheduler.py — Unit tests for DBDSPScheduler + RTX-CL."""

import pytest

from netrescue.transfer.scheduler import DBDSPScheduler, PathState, PathStatus


def make_paths(**kwargs) -> list[PathState]:
    """Return [p1(fast), p2(slow)] by default."""
    p1 = PathState("p1", fd_estimate_ms=kwargs.get("fd1", 10.0), tp_estimate_bps=1_000_000)
    p2 = PathState("p2", fd_estimate_ms=kwargs.get("fd2", 100.0), tp_estimate_bps=500_000)
    return [p1, p2]


class TestDBDSPSelection:
    def test_selects_lowest_arrival_time_path(self):
        sched = DBDSPScheduler(make_paths(fd1=10, fd2=100))
        # p1 has lower FD and equal chunk_size → should be selected
        pid = sched.select_path(chunk_size_bytes=1024)
        assert pid == "p1"

    def test_returns_none_when_all_paths_down(self):
        paths = make_paths()
        sched = DBDSPScheduler(paths)
        sched.mark_path_down("p1")
        sched.mark_path_down("p2")
        assert sched.select_path() is None

    def test_respects_exclude_set(self):
        sched = DBDSPScheduler(make_paths(fd1=10, fd2=100))
        pid = sched.select_path(exclude={"p1"})
        assert pid == "p2"

    def test_excludes_down_paths_automatically(self):
        sched = DBDSPScheduler(make_paths())
        sched.mark_path_down("p1")
        pid = sched.select_path()
        assert pid == "p2"


class TestEWMAUpdates:
    def test_record_ack_reduces_fd_estimate_on_fast_ack(self):
        paths = make_paths(fd1=100.0)
        sched = DBDSPScheduler(paths)
        initial_fd = paths[0].fd_estimate_ms
        # Simulate fast ACK (RTT/2 = 5ms < 100ms)
        sched.record_ack("p1", rtt_ms=10.0, size_bytes=4096)
        assert paths[0].fd_estimate_ms < initial_fd

    def test_record_ack_increases_tp_estimate_on_large_payload(self):
        paths = make_paths()
        initial_tp = paths[0].tp_estimate_bps
        sched = DBDSPScheduler(paths)
        sched.record_ack("p1", rtt_ms=1.0, size_bytes=100_000)
        # TP sample = 100000 * 8 / 0.001 = 800 Mbps >> initial 1 Mbps
        assert paths[0].tp_estimate_bps > initial_tp


class TestFailureTracking:
    def test_path_marked_down_after_max_failures(self):
        paths = make_paths()
        sched = DBDSPScheduler(paths, max_consecutive_failures=3)
        for _ in range(3):
            sched.record_failure("p1")
        assert paths[0].status == PathStatus.DOWN

    def test_path_stays_up_before_max_failures(self):
        paths = make_paths()
        sched = DBDSPScheduler(paths, max_consecutive_failures=3)
        for _ in range(2):
            sched.record_failure("p1")
        assert paths[0].status == PathStatus.UP

    def test_mark_path_up_restores_path(self):
        paths = make_paths()
        sched = DBDSPScheduler(paths)
        sched.mark_path_down("p1")
        sched.mark_path_up("p1")
        assert paths[0].status == PathStatus.UP


class TestRTXCL:
    def test_rtx_selects_alternate_path(self):
        sched = DBDSPScheduler(make_paths())
        # Primary path p1 failed; RTX should pick p2
        pid = sched.select_rtx_path(exclude={"p1"})
        assert pid == "p2"

    def test_rtx_returns_none_when_all_excluded(self):
        sched = DBDSPScheduler(make_paths())
        pid = sched.select_rtx_path(exclude={"p1", "p2"})
        assert pid is None

    def test_rtx_prefers_high_cwnd_for_similar_delays(self):
        p1 = PathState("p1", fd_estimate_ms=10.0, cwnd_estimate_bytes=65536)
        p2 = PathState("p2", fd_estimate_ms=11.0, cwnd_estimate_bytes=131072)  # same delay band
        sched = DBDSPScheduler([p1, p2])
        # Both within 5ms window; should pick p2 (higher cwnd)
        pid = sched.select_rtx_path()
        assert pid == "p2"


class TestUpPaths:
    def test_up_path_ids(self):
        paths = make_paths()
        sched = DBDSPScheduler(paths)
        assert set(sched.up_path_ids) == {"p1", "p2"}

    def test_up_path_ids_excludes_down(self):
        paths = make_paths()
        sched = DBDSPScheduler(paths)
        sched.mark_path_down("p1")
        assert sched.up_path_ids == ["p2"]


class TestPathSnapshot:
    def test_snapshot_includes_all_paths(self):
        sched = DBDSPScheduler(make_paths())
        snap = sched.path_snapshot()
        assert len(snap) == 2
        ids = {s["path_id"] for s in snap}
        assert "p1" in ids and "p2" in ids
