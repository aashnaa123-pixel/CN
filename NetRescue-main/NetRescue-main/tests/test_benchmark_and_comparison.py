"""
tests/test_benchmark_and_comparison.py — Feature D measurement (PRD §6.2, §6.5).

The previous BenchmarkRunner._run_udp set received_count = message_count as a
placeholder and never read a reply, so every RTT statistic came back None.
These tests assert the runner actually measures.
"""

import asyncio

import pytest

from netrescue.benchmark.comparison import (
    ALL_RETRANSMIT_POLICIES,
    ALL_VARIANTS,
    ComparisonConfig,
    PathSpec,
    SchedulerComparisonHarness,
)
from netrescue.benchmark.report import ComparisonRow, render_table, summarise, to_csv
from netrescue.benchmark.runner import BenchmarkConfig, BenchmarkRunner


def free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestUdpBenchmark:
    @pytest.mark.asyncio
    async def test_measures_rtt(self):
        port = free_port()
        cfg = BenchmarkConfig(
            run_id="bench-udp", sender_host="127.0.0.1", sender_port=port,
            target_host="127.0.0.1", target_port=port,
            message_count=20, message_size_bytes=64, send_interval_s=0.001,
        )
        results = await BenchmarkRunner(cfg).run()

        assert results.received_count == 20
        assert results.lost_count == 0
        assert len(results.rtt_samples_ms) == 20
        assert results.rtt_median_ms is not None and results.rtt_median_ms > 0
        assert results.rtt_p95_ms is not None
        assert results.jitter_ms is not None

    @pytest.mark.asyncio
    async def test_echo_arrivals_match_sender_view(self):
        port = free_port()
        cfg = BenchmarkConfig(
            run_id="bench-cross", sender_host="127.0.0.1", sender_port=port,
            target_host="127.0.0.1", target_port=port,
            message_count=15, message_size_bytes=32, send_interval_s=0.001,
        )
        results = await BenchmarkRunner(cfg).run()
        assert results.echo_arrivals == results.received_count

    @pytest.mark.asyncio
    async def test_records_environment_and_semantics(self):
        port = free_port()
        cfg = BenchmarkConfig(
            run_id="bench-env", sender_host="127.0.0.1", sender_port=port,
            target_host="127.0.0.1", target_port=port, message_count=5,
            message_size_bytes=32, send_interval_s=0.001,
        )
        summary = (await BenchmarkRunner(cfg).run()).summary()

        assert summary["environment"]["os"]
        assert summary["environment"]["kernel"]
        assert summary["environment"]["emulated"] is True
        assert "application-message loss" in summary["measurement_semantics"]["loss"]
        assert "not real-world" in summary["measurement_semantics"]["note"]

    @pytest.mark.asyncio
    async def test_csv_export_has_samples(self):
        port = free_port()
        cfg = BenchmarkConfig(
            run_id="bench-csv", sender_host="127.0.0.1", sender_port=port,
            target_host="127.0.0.1", target_port=port, message_count=8,
            message_size_bytes=32, send_interval_s=0.001,
        )
        results = await BenchmarkRunner(cfg).run()
        csv_text = results.to_csv()
        assert csv_text.startswith("run_id,label,protocol,seq,rtt_ms")
        assert len(csv_text.strip().splitlines()) == results.received_count + 1

    @pytest.mark.asyncio
    async def test_tcp_protocol_works(self):
        port = free_port()
        cfg = BenchmarkConfig(
            run_id="bench-tcp", sender_host="127.0.0.1", sender_port=port,
            target_host="127.0.0.1", target_port=port, protocol="TCP",
            message_count=10, message_size_bytes=64, send_interval_s=0.0,
        )
        results = await BenchmarkRunner(cfg).run()
        assert results.received_count == 10
        assert len(results.rtt_samples_ms) == 10

    @pytest.mark.asyncio
    async def test_unknown_protocol_rejected(self):
        port = free_port()
        cfg = BenchmarkConfig(
            run_id="bench-bad", sender_host="127.0.0.1", sender_port=port,
            target_host="127.0.0.1", target_port=port, protocol="SCTP",
        )
        with pytest.raises(ValueError):
            await BenchmarkRunner(cfg).run()


class TestReportHelpers:
    def test_summarise_median_and_p95(self):
        s = summarise([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        assert s["median"] == 5.5
        assert s["min"] == 1 and s["max"] == 10
        assert s["p95"] >= s["median"]

    def test_summarise_empty(self):
        assert summarise([])["median"] is None

    def test_summarise_single(self):
        assert summarise([7.0])["p95"] == 7.0

    def test_table_and_csv_render(self):
        row = ComparisonRow(
            variant="DB-DSP", retransmit_policy="RTX-CL", repetitions=5,
            duration_s=summarise([1.0, 1.1, 1.2, 1.3, 1.4]),
            out_of_order_count=summarise([2, 1, 3, 0, 2]),
            integrity_ok_count=5,
            per_path_utilisation={"p1": 10, "p2": 9},
        )
        table = render_table([row])
        assert "DB-DSP" in table and "RTX-CL" in table

        csv_text = to_csv([row])
        assert "variant" in csv_text.splitlines()[0]
        assert "p1=10" in csv_text


class TestSchedulerComparison:
    def test_default_matrix_matches_prd(self):
        assert set(ALL_VARIANTS) == {"DB-DSP", "RR", "WRR", "SINGLE", "NAIVE_DELAY"}
        assert set(ALL_RETRANSMIT_POLICIES) == {"RTX-CL", "RTX-CWND"}

    def test_defaults_require_five_repetitions(self):
        cfg = ComparisonConfig(run_id="c", paths=[PathSpec("p1")])
        assert cfg.repetitions >= 5

    def test_path_spec_produces_independent_state(self):
        a = PathSpec("p1", fd_ms=10, tp_bps=1e6).to_path_state()
        b = PathSpec("p1", fd_ms=10, tp_bps=1e6).to_path_state()
        a.update_from_ack(50.0, 1000)
        assert b.fd_estimate_ms != a.fd_estimate_ms, "specs must not share mutable state"

    def test_workload_records_heterogeneity(self):
        cfg = ComparisonConfig(
            run_id="c",
            paths=[PathSpec("p1", fd_ms=10), PathSpec("p2", fd_ms=100)],
        )
        assert cfg.workload()["delay_heterogeneity"] == "10.0:1"

    @pytest.mark.asyncio
    async def test_small_matrix_produces_one_row_per_cell(self):
        cfg = ComparisonConfig(
            run_id="cmp-small",
            paths=[
                PathSpec("p1", fd_ms=10.0, tp_bps=8e6),
                PathSpec("p2", fd_ms=50.0, tp_bps=4e6),
                PathSpec("p3", fd_ms=100.0, tp_bps=2e6),
            ],
            payload_bytes=40_000,
            chunk_size_bytes=4096,
            repetitions=5,
            variants=["DB-DSP", "RR"],
            retransmit_policies=["RTX-CL"],
        )
        report = await SchedulerComparisonHarness(cfg).run()

        assert len(report.rows) == 2
        for row in report.rows:
            assert row.repetitions == 5
            assert row.integrity_ok_count == 5
            assert row.duration_s["median"] is not None
            assert row.out_of_order_count["median"] is not None
        assert report.findings
        assert report.limitations

    @pytest.mark.asyncio
    async def test_full_matrix_serialises(self):
        cfg = ComparisonConfig(
            run_id="cmp-full",
            paths=[PathSpec("p1", fd_ms=10.0), PathSpec("p2", fd_ms=60.0)],
            payload_bytes=20_000, chunk_size_bytes=4096, repetitions=5,
        )
        report = await SchedulerComparisonHarness(cfg).run()

        # 5 variants x 2 policies
        assert len(report.rows) == 10
        data = report.to_dict()
        assert len(data["rows"]) == 10
        assert report.table()
        assert report.to_csv().count("\n") == 11  # header + 10 rows