"""tests/test_transfer_session.py — Integration tests for MultipathTransferSession."""

import asyncio
import hashlib

import pytest

from netrescue.transfer.scheduler import PathState
from netrescue.transfer.session import MultipathTransferSession, SchedulerVariant


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_paths(n: int = 2, fd_ms_list: list[float] | None = None, loss: float = 0.0) -> list[PathState]:
    fd_list = fd_ms_list or [10.0, 50.0][:n]
    return [
        PathState(f"p{i}", fd_estimate_ms=fd_list[i % len(fd_list)], tp_estimate_bps=1_000_000, loss_rate=loss)
        for i in range(n)
    ]


class TestDBDSPTransfer:
    def test_small_payload_integrity(self):
        data = b"Hello, NetRescue!" * 100
        session = MultipathTransferSession(
            run_id="r1", transfer_id="t1",
            paths=make_paths(2), scheduler_variant="DB-DSP",
        )
        result = asyncio.run(session.transfer(data))
        assert result.integrity_ok
        assert result.source_sha256 == sha256(data)
        assert result.receiver_sha256 == result.source_sha256

    def test_large_payload_integrity(self):
        data = b"X" * 1_000_000  # 1 MB
        session = MultipathTransferSession(
            run_id="r2", transfer_id="t2",
            paths=make_paths(3, [10.0, 30.0, 80.0]),
            chunk_size_bytes=65536,
            scheduler_variant="DB-DSP",
        )
        result = asyncio.run(session.transfer(data))
        assert result.integrity_ok

    def test_all_chunks_delivered(self):
        data = b"chunk" * 500
        session = MultipathTransferSession(
            run_id="r3", transfer_id="t3",
            paths=make_paths(2), scheduler_variant="DB-DSP",
        )
        result = asyncio.run(session.transfer(data))
        assert result.failed_chunks == 0
        assert result.delivered_chunks == result.total_chunks


class TestRoundRobinTransfer:
    def test_rr_integrity(self):
        data = b"RR test data" * 200
        session = MultipathTransferSession(
            run_id="r4", transfer_id="t4",
            paths=make_paths(2), scheduler_variant="RR",
        )
        result = asyncio.run(session.transfer(data))
        assert result.integrity_ok


class TestSinglePathTransfer:
    def test_single_path_uses_lowest_fd(self):
        data = b"single path" * 100
        session = MultipathTransferSession(
            run_id="r5", transfer_id="t5",
            paths=make_paths(2, [10.0, 100.0]),
            scheduler_variant="SINGLE",
        )
        result = asyncio.run(session.transfer(data))
        assert result.integrity_ok
        # All chunks should go to p0 (fd=10ms)
        assert result.per_path_utilisation.get("p0", 0) > 0


class TestPathInterruption:
    def test_transfer_survives_one_path_failure(self):
        """With 2 paths and one marked DOWN, transfer should still succeed."""
        paths = make_paths(2, [10.0, 50.0], loss=0.0)
        session = MultipathTransferSession(
            run_id="r6", transfer_id="t6",
            paths=paths, scheduler_variant="DB-DSP",
        )
        # Mark p0 as DOWN before transfer
        session._scheduler.mark_path_down("p0")
        data = b"interrupted path test" * 50
        result = asyncio.run(session.transfer(data))
        assert result.integrity_ok


class TestBoundedFailure:
    def test_bounded_failure_when_no_paths(self):
        """With all paths DOWN, transfer should report bounded failure, not hang."""
        paths = make_paths(2, loss=0.0)
        session = MultipathTransferSession(
            run_id="r7", transfer_id="t7",
            paths=paths, max_retries=1,
            scheduler_variant="DB-DSP",
        )
        session._scheduler.mark_path_down("p0")
        session._scheduler.mark_path_down("p1")
        data = b"will fail" * 10
        result = asyncio.run(session.transfer(data))
        # Should fail cleanly without raising
        assert result.failed_chunks > 0
        assert not result.integrity_ok


class TestPerPathUtilisation:
    def test_per_path_utilisation_totals_delivered_chunks(self):
        data = b"utilization test" * 200
        session = MultipathTransferSession(
            run_id="r8", transfer_id="t8",
            paths=make_paths(2), scheduler_variant="DB-DSP",
        )
        result = asyncio.run(session.transfer(data))
        total = sum(result.per_path_utilisation.values())
        assert total == result.delivered_chunks


class TestWWRAndNaiveDelay:
    def test_wwr_integrity(self):
        data = b"WRR" * 300
        session = MultipathTransferSession(
            run_id="r9", transfer_id="t9",
            paths=make_paths(3), scheduler_variant="WRR",
        )
        result = asyncio.run(session.transfer(data))
        assert result.integrity_ok

    def test_naive_delay_integrity(self):
        data = b"NAIVE" * 200
        session = MultipathTransferSession(
            run_id="r10", transfer_id="t10",
            paths=make_paths(2), scheduler_variant="NAIVE_DELAY",
        )
        result = asyncio.run(session.transfer(data))
        assert result.integrity_ok
