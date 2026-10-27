"""
tests/test_transfer_pipelining.py — pipelined delivery and real reordering
measurement (PRD §5.4, §11).

The previous session implementation delivered chunks strictly one at a time and
computed `out_of_order_count` from a pre-sorted list, so it was structurally
incapable of producing reordering. These tests pin the new behaviour.
"""

import asyncio

import pytest

from netrescue.transfer.scheduler import PathState
from netrescue.transfer.session import MultipathTransferSession, SchedulerVariant


def hetero_paths(loss: float = 0.0, n: int = 3):
    """
    Paths with a wide delay spread *and* uneven bandwidth.

    Both dimensions matter: with uniform bandwidth DB-DSP's arrival-time ranking
    collapses to "lowest delay wins" and there is nothing for multipath to
    exploit, so the comparison would not test what it claims to.
    """
    specs = [
        ("p0", 10.0, 8_000_000),
        ("p1", 50.0, 4_000_000),
        ("p2", 100.0, 2_000_000),
        ("p3", 25.0, 6_000_000),
        ("p4", 75.0, 3_000_000),
    ][:n]
    return [
        PathState(pid, fd_estimate_ms=fd, tp_estimate_bps=tp, loss_rate=loss)
        for pid, fd, tp in specs
    ]


class TestOutOfOrderMetric:
    def test_counting_is_not_trivially_zero(self):
        count = MultipathTransferSession._count_out_of_order
        assert count([]) == 0
        assert count([0, 1, 2, 3]) == 0
        # 3 arrives before 1 and 2 -> two late chunks
        assert count([0, 3, 1, 2]) == 2
        # pure reversal -> everything after the first is late
        assert count([4, 3, 2, 1, 0]) == 4

    def test_high_water_mark_is_monotonic(self):
        count = MultipathTransferSession._count_out_of_order
        assert count([5, 6, 1, 7, 2]) == 2


class TestPipelinedDelivery:
    @pytest.mark.asyncio
    async def test_multipath_aggregates_bandwidth(self):
        """
        A load-spreading scheduler must move the same payload faster than one
        pinned to a single link, because parallel paths aggregate bandwidth.

        Paths are serialised resources, so N paths deliver ~N x the throughput.
        Weighted Round-Robin is used as the spreading policy because DB-DSP is
        a greedy arrival-time minimiser: with one path dominating on both delay
        and bandwidth it legitimately keeps choosing that path.
        """
        payload = b"Z" * 120_000
        common = dict(
            run_id="r", paths=hetero_paths(), chunk_size_bytes=8192, seed=5,
        )
        spread = await MultipathTransferSession(
            transfer_id="wrr", scheduler_variant="WRR", **common
        ).transfer(payload)
        pinned = await MultipathTransferSession(
            transfer_id="single", scheduler_variant="SINGLE", **common
        ).transfer(payload)

        assert spread.integrity_ok and pinned.integrity_ok
        assert spread.duration_s < pinned.duration_s, (
            f"multipath {spread.duration_s:.3f}s should beat "
            f"single-path {pinned.duration_s:.3f}s"
        )
        assert spread.goodput_bps > pinned.goodput_bps

    @pytest.mark.asyncio
    async def test_paths_are_serialised_so_bandwidth_is_finite(self):
        """Concurrency must not make bandwidth free."""
        payload = b"W" * 200_000
        session = MultipathTransferSession(
            run_id="r", transfer_id="ser", paths=hetero_paths(),
            chunk_size_bytes=8192, scheduler_variant="SINGLE", seed=5,
        )
        result = await session.transfer(payload)
        assert result.integrity_ok
        # One path at ~8 Mbps for 200 KB cannot complete near-instantly.
        assert result.duration_s > 0.05

    @pytest.mark.asyncio
    async def test_reordering_is_actually_observed(self):
        """Arrival order must diverge from dispatch order under delay spread."""
        payload = b"Q" * 200_000
        result = await MultipathTransferSession(
            run_id="r", transfer_id="ooo",
            paths=hetero_paths(), chunk_size_bytes=4096,
            scheduler_variant="RR", in_flight=32,
        ).transfer(payload)

        assert result.integrity_ok
        assert result.arrival_order, "arrival order must be recorded"
        assert len(set(result.arrival_order)) == result.total_chunks
        assert result.out_of_order_count > 0, (
            "round-robin over 10/50/100 ms paths must produce reordering"
        )

    @pytest.mark.asyncio
    async def test_db_dsp_beats_naive_delay_ablation(self):
        """
        The ablation must isolate throughput awareness.

        Paths have near-identical forward delay but wildly different bandwidth.
        A pure-delay policy picks the 10 ms / 1 Mbps path and stalls on it;
        DB-DSP picks the 12 ms / 50 Mbps path and finishes far sooner. If these
        two agree, the ablation is measuring nothing.
        """
        specs = [PathState("p0", fd_estimate_ms=10.0, tp_estimate_bps=1_000_000),
                 PathState("p1", fd_estimate_ms=12.0, tp_estimate_bps=50_000_000)]
        payload = b"A" * 160_000

        dsp = await MultipathTransferSession(
            run_id="r", transfer_id="dsp", paths=specs,
            chunk_size_bytes=32768, scheduler_variant="DB-DSP", seed=1000,
        ).transfer(payload)
        naive = await MultipathTransferSession(
            run_id="r", transfer_id="naive", paths=specs,
            chunk_size_bytes=32768, scheduler_variant="NAIVE_DELAY", seed=1000,
        ).transfer(payload)

        assert dsp.integrity_ok and naive.integrity_ok
        assert dsp.duration_s < naive.duration_s * 0.5, (
            f"DB-DSP {dsp.duration_s:.3f}s vs naive {naive.duration_s:.3f}s — "
            f"the ablation is not discriminating"
        )
        assert dsp.goodput_bps > naive.goodput_bps * 2

    @pytest.mark.asyncio
    async def test_comparison_harness_reports_the_prd_target_verbatim(self):
        """
        The harness must evaluate PRD §11 and report PASS or FAIL from
        measurement — never assert the research claim as a test invariant.

        Whether DB-DSP clears "out-of-order <= 50% of round-robin" is an
        empirical result that depends on chunk size and topology, so the test
        pins the *reporting* rather than the verdict.
        """
        from netrescue.benchmark.comparison import (
            ComparisonConfig, PathSpec, SchedulerComparisonHarness,
        )

        cfg = ComparisonConfig(
            run_id="ooo-check",
            paths=[
                PathSpec("p0", fd_ms=10.0, tp_bps=1_000_000),
                PathSpec("p1", fd_ms=12.0, tp_bps=50_000_000),
                PathSpec("p2", fd_ms=14.0, tp_bps=50_000_000),
            ],
            payload_bytes=120_000, chunk_size_bytes=32768, repetitions=5,
            variants=["DB-DSP", "RR"], retransmit_policies=["RTX-CL"],
        )
        report = await SchedulerComparisonHarness(cfg).run()

        findings = [f for f in report.findings if "out-of-order" in f.lower()]
        assert findings, f"the harness must always report the comparison; got {report.findings}"
        verdict = findings[0]
        assert "DB-DSP" in verdict and "round-robin" in verdict
        assert any(tag in verdict for tag in ("PASS", "target not met", "not evaluable"))

        # Whatever the verdict, the numbers behind it must be real.
        for row in report.rows:
            assert row.out_of_order_count["median"] is not None
            assert row.integrity_ok_count == row.repetitions

    @pytest.mark.asyncio
    async def test_integration_still_preserves_payload(self):
        payload = bytes(range(256)) * 400
        for variant in ("DB-DSP", "RR", "WRR", "SINGLE", "NAIVE_DELAY"):
            result = await MultipathTransferSession(
                run_id="r", transfer_id=variant, paths=hetero_paths(),
                chunk_size_bytes=4096, scheduler_variant=variant, seed=42,
            ).transfer(payload)
            assert result.integrity_ok, f"{variant} corrupted the payload"
            assert result.failed_chunks == 0


class TestPerPathUtilisation:
    @pytest.mark.asyncio
    async def test_utilisation_totals_delivered_chunks(self):
        payload = b"U" * 150_000
        result = await MultipathTransferSession(
            run_id="r", transfer_id="u", paths=hetero_paths(),
            chunk_size_bytes=4096, scheduler_variant="DB-DSP", seed=1,
        ).transfer(payload)
        assert sum(result.per_path_utilisation.values()) == result.delivered_chunks

    @pytest.mark.asyncio
    async def test_single_path_uses_only_one_path(self):
        payload = b"S" * 100_000
        result = await MultipathTransferSession(
            run_id="r", transfer_id="s", paths=hetero_paths(),
            chunk_size_bytes=4096, scheduler_variant="SINGLE", seed=1,
        ).transfer(payload)
        used = [p for p, n in result.per_path_utilisation.items() if n > 0]
        assert len(used) == 1, f"SINGLE must pin to one path, used {used}"


class TestBoundedFailure:
    @pytest.mark.asyncio
    async def test_all_paths_down_reports_bounded_failure(self):
        session = MultipathTransferSession(
            run_id="r", transfer_id="f", paths=hetero_paths(),
            max_retries=1, scheduler_variant="DB-DSP",
        )
        for p in session._paths:
            session._scheduler.mark_path_down(p)
        result = await session.transfer(b"data" * 100)
        assert result.failed_chunks > 0
        assert not result.integrity_ok

    @pytest.mark.asyncio
    async def test_loss_under_retries_still_delivers(self):
        payload = b"L" * 120_000
        result = await MultipathTransferSession(
            run_id="r", transfer_id="l", paths=hetero_paths(loss=0.05),
            chunk_size_bytes=4096, scheduler_variant="DB-DSP",
            max_retries=5, seed=99,
        ).transfer(payload)
        assert result.retransmission_count > 0, "loss should have caused retransmits"
        assert result.integrity_ok, "retries must still produce a correct file"


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_same_seed_same_result(self):
        payload = b"D" * 80_000
        outs = []
        for _ in range(2):
            result = await MultipathTransferSession(
                run_id="r", transfer_id="d", paths=hetero_paths(loss=0.03),
                chunk_size_bytes=4096, scheduler_variant="DB-DSP", seed=7,
            ).transfer(payload)
            outs.append((result.out_of_order_count, result.retransmission_count))
        assert outs[0] == outs[1]