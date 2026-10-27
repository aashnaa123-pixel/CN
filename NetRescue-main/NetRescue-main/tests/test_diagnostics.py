"""
tests/test_diagnostics.py — Feature 3: Windows network diagnostics & benchmarking.

Two kinds of test, deliberately separated:

  * **Pure unit tests** over the parsers, the configuration hash and the seeded
    fault simulator. These need no network at all and pin the exact behaviour of
    the code that turns a command's output into numbers.
  * **HTTP tests** over the real FastAPI app, using loopback targets (`127.0.0.1`)
    and short timeouts. They assert that a real command ran and that the
    response distinguishes real measurements, simulations and partial runs.

The database dependency is overridden to ``None`` for the HTTP tests so a run is
never written to whatever PostgreSQL the developer happens to have configured.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from apps.api.deps import get_optional_db
from apps.api.main import app
from netrescue.diagnostics import (
    BenchmarkEngine,
    BenchmarkSpec,
    FaultScenario,
    compare_baseline,
    compare_runs,
    config_hash,
    diagnostics_store,
    feature_b_side_by_side,
    measurement_report,
    parse_ping_output,
    parse_tracert_output,
    run_fault_scenario,
    run_to_csv,
    validate_target,
)
from netrescue.diagnostics.probes import ProbeError

OPERATOR = {"X-NetRescue-Principal": "operator", "X-NetRescue-Role": "operator"}
READONLY = {"X-NetRescue-Principal": "viewer", "X-NetRescue-Role": "readonly"}

WIN_PING = """\
Pinging 127.0.0.1 with 32 bytes of data:
Reply from 127.0.0.1: bytes=32 time<1ms TTL=128
Reply from 127.0.0.1: bytes=32 time<1ms TTL=128
Reply from 127.0.0.1: bytes=32 time=3ms TTL=128
Reply from 127.0.0.1: bytes=32 time=1ms TTL=128

Ping statistics for 127.0.0.1:
    Packets: Sent = 4, Received = 4, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 0ms, Maximum = 3ms, Average = 1ms
"""

WIN_PING_LOSS = """\
Pinging 10.255.255.1 with 32 bytes of data:
Request timed out.
Request timed out.

Ping statistics for 10.255.255.1:
    Packets: Sent = 2, Received = 0, Lost = 2 (100% loss),
"""

POSIX_PING = """\
PING example.com (93.184.216.34) 56(84) bytes of data.
64 bytes from 93.184.216.34: icmp_seq=1 ttl=56 time=11.2 ms
64 bytes from 93.184.216.34: icmp_seq=2 ttl=56 time=10.8 ms

--- example.com ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1001ms
rtt min/avg/max/mdev = 10.812/11.006/11.200/0.194 ms
"""

WIN_TRACERT = """\
Tracing route to 8.8.8.8 over a maximum of 30 hops

  1    <1 ms    <1 ms    <1 ms  192.168.1.1
  2    12 ms    11 ms    13 ms  100.64.0.1
  3     *        *        *     Request timed out.
  4    24 ms    23 ms    24 ms  dns.google [8.8.8.8]

Trace complete.
"""


# ---------------------------------------------------------------------------
# Configuration identity
# ---------------------------------------------------------------------------


class TestConfigHash:
    def test_key_order_does_not_change_the_hash(self):
        assert config_hash({"b": 1, "a": [2, 3]}) == config_hash({"a": [2, 3], "b": 1})

    def test_list_order_does_change_the_hash(self):
        # Ratio order and path order are part of the configuration.
        assert config_hash({"ratios": [1.0, 2.0]}) != config_hash({"ratios": [2.0, 1.0]})

    def test_a_changed_value_changes_the_hash(self):
        assert config_hash({"count": 4}) != config_hash({"count": 5})

    def test_negative_zero_matches_zero(self):
        assert config_hash({"x": 0.0}) == config_hash({"x": -0.0})

    def test_hash_is_sha256_hex(self):
        digest = config_hash({"a": 1})
        assert len(digest) == 64
        int(digest, 16)  # must parse as hex


# ---------------------------------------------------------------------------
# Target validation
# ---------------------------------------------------------------------------


class TestValidateTarget:
    @pytest.mark.parametrize("value", ["127.0.0.1", "8.8.8.8", "example.com", "a-b.example.co.uk"])
    def test_accepts_valid_targets(self, value):
        assert validate_target(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "8.8.8.8 & del *",
            "8.8.8.8;rm",
            "host|nc",
            "",
            "-oops",
            "a" * 300,
            "bad host",
        ],
    )
    def test_rejects_injection_and_malformed_input(self, value):
        with pytest.raises(ProbeError) as exc:
            validate_target(value)
        assert exc.value.kind == "invalid_input"


# ---------------------------------------------------------------------------
# Ping parser
# ---------------------------------------------------------------------------


class TestParsePing:
    def test_windows_output(self):
        r = parse_ping_output("127.0.0.1", WIN_PING)
        assert (r.sent, r.received) == (4, 4)
        assert r.loss_rate == 0.0
        assert r.sub_millisecond_samples == 2
        # Sub-millisecond replies are recorded as 0, not invented upward.
        assert r.samples_ms == [0.0, 0.0, 3.0, 1.0]
        assert r.min_ms == 0.0
        assert r.max_ms == 3.0
        assert r.stddev_ms is not None

    def test_windows_total_loss(self):
        r = parse_ping_output("10.255.255.1", WIN_PING_LOSS)
        assert (r.sent, r.received) == (2, 0)
        assert r.reachable is False
        assert r.loss_rate == 1.0
        assert r.avg_ms is None

    def test_posix_output(self):
        r = parse_ping_output("example.com", POSIX_PING)
        assert (r.sent, r.received) == (2, 2)
        assert r.loss_rate == 0.0
        # Statistics are computed from the individual probes and rounded to 3dp.
        assert r.min_ms == 10.8
        assert r.max_ms == 11.2
        assert r.avg_ms == 11.0

    def test_notes_explain_icmp_caveats(self):
        r = parse_ping_output("127.0.0.1", WIN_PING)
        assert any("ICMP" in note for note in r.notes)

    def test_serialises_units(self):
        payload = parse_ping_output("127.0.0.1", WIN_PING).to_dict()
        assert payload["units"] == "ms"
        assert payload["loss_pct"] == "0.0%"
        assert payload["reachable"] is True


# ---------------------------------------------------------------------------
# Tracert parser
# ---------------------------------------------------------------------------


class TestParseTracert:
    def test_parses_hops_and_marks_silent_ones(self):
        parsed = parse_tracert_output("8.8.8.8", WIN_TRACERT, max_hops=30)
        assert parsed["hop_count"] == 4
        hops = {h["hop"]: h for h in parsed["hops"]}
        assert hops[1]["node"] == "192.168.1.1"
        assert hops[1]["sub_millisecond"] is True
        assert hops[3]["timed_out"] is True
        assert hops[3]["rtt_ms"] is None
        assert hops[4]["node"] == "dns.google [8.8.8.8]"
        assert hops[4]["rtt_ms"] == 24.0

    def test_respects_max_hops(self):
        parsed = parse_tracert_output("8.8.8.8", WIN_TRACERT, max_hops=2)
        assert parsed["hop_count"] == 2


# ---------------------------------------------------------------------------
# Deterministic fault simulator
# ---------------------------------------------------------------------------


class TestFaultSimulator:
    def test_same_seed_is_reproducible(self):
        a = run_fault_scenario(FaultScenario(scenario="packet_loss", seed=42, count=5, repetitions=3)).to_dict()
        b = run_fault_scenario(FaultScenario(scenario="packet_loss", seed=42, count=5, repetitions=3)).to_dict()
        assert a["config_hash"] == b["config_hash"]
        assert a["probes"] == b["probes"]

    def test_different_seed_diverges(self):
        a = run_fault_scenario(FaultScenario(scenario="packet_loss", seed=1, count=10, repetitions=3))
        b = run_fault_scenario(FaultScenario(scenario="packet_loss", seed=2, count=10, repetitions=3))
        assert a.to_dict()["probes"] != b.to_dict()["probes"]

    def test_unreachable_has_no_healthy_probe(self):
        result = run_fault_scenario(
            FaultScenario(scenario="unreachable", seed=1, count=4, repetitions=2)
        ).to_dict()
        assert result["healthy_count"] == 0
        assert result["unhealthy_count"] == 8
        assert result["simulated"] is True
        assert result["data_kind"] == "simulated"

    def test_intermittent_produces_transitions(self):
        result = run_fault_scenario(
            FaultScenario(scenario="intermittent", seed=3, count=6, repetitions=3, period=6)
        ).to_dict()
        assert result["transition_count"] >= 2
        assert result["healthy_count"] > 0
        assert result["unhealthy_count"] > 0

    def test_unknown_scenario_is_rejected(self):
        with pytest.raises(ValueError):
            FaultScenario(scenario="not-a-scenario")

    def test_invalid_loss_rate_is_rejected(self):
        with pytest.raises(ValueError):
            FaultScenario(scenario="packet_loss", loss_rate=2.0)

    def test_limitations_call_it_simulated(self):
        result = run_fault_scenario(FaultScenario(scenario="packet_loss", seed=1, count=2, repetitions=1))
        assert any("SIMULATED" in item for item in result.to_dict()["limitations"])


# ---------------------------------------------------------------------------
# Real benchmark against loopback
# ---------------------------------------------------------------------------


class TestBenchmarkEngine:
    async def test_measures_loopback(self):
        spec = BenchmarkSpec(target="127.0.0.1", count=2, timeout_s=2.0, repetitions=2)
        run = await BenchmarkEngine(spec).run()
        payload = run.to_dict()
        assert payload["simulated"] is False
        assert payload["source"] == "real_ping"
        assert payload["aggregate"]["sent"] == 4
        assert payload["aggregate"]["received"] == 4
        assert payload["aggregate"]["avg_ms"] is not None
        assert payload["status"] == "DONE"

    async def test_config_hash_is_stable_across_runs(self):
        spec = BenchmarkSpec(target="127.0.0.1", count=1, timeout_s=2.0, repetitions=1)
        a = await BenchmarkEngine(spec).run()
        b = await BenchmarkEngine(spec).run()
        assert a.config_hash == b.config_hash
        assert a.run_id != b.run_id

    async def test_csv_export_has_a_row_per_repetition_plus_aggregate(self):
        spec = BenchmarkSpec(target="127.0.0.1", count=2, timeout_s=2.0, repetitions=2)
        run = await BenchmarkEngine(spec).run()
        csv_text = run.to_csv()
        assert "AGGREGATE" in csv_text
        assert run.run_id in csv_text
        assert csv_text.strip().splitlines()[0].startswith("run_id")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class TestReporting:
    def _real_run(self):
        return {
            "run_id": "r-real",
            "kind": "benchmark",
            "simulated": False,
            "status": "DONE",
            "config": {"target": "127.0.0.1"},
            "config_hash": "a" * 64,
            "software_version": "1.0.0",
            "environment": {},
            "aggregate": {
                "sent": 4, "received": 4, "lost": 0, "packet_loss_rate": 0.0,
                "min_ms": 0.0, "avg_ms": 1.0, "max_ms": 3.0, "stddev_ms": 1.0,
                "median_ms": 1.0,
            },
        }

    def _sim_run(self):
        run = dict(self._real_run())
        run.update(run_id="r-sim", simulated=True, config_hash="b" * 64)
        return run

    def test_measurement_report_labels_data_kind(self):
        report = measurement_report(self._real_run())
        assert report["data_kind"] == "real"
        assert report["results"]["units"] == {"latency": "ms", "loss": "percent"}

    def test_comparison_has_no_combined_score(self):
        comparison = compare_runs([self._real_run(), self._sim_run()])
        assert comparison["run_count"] == 2
        assert set(comparison["data_kinds"]) == {"real", "simulated"}
        # No row may expose a single ranking number...
        for row in comparison["runs"]:
            assert "score" not in row
            assert "rank" not in row
        # ...and the report says why it refuses to produce one.
        assert any("no combined score" in note.lower() for note in comparison["notes"])

    def test_csv_of_a_dict_run_is_well_formed(self):
        csv_text = run_to_csv(self._real_run())
        assert "AGGREGATE" in csv_text
        assert csv_text.strip().splitlines()[0].startswith("run_id")


# ---------------------------------------------------------------------------
# Feature A / Feature B integration shaping
# ---------------------------------------------------------------------------


class TestIntegrationShapes:
    def test_baseline_comparison_refuses_to_claim_causality(self):
        before = {
            "run_id": "before", "phase": "before", "config_hash": "h",
            "aggregate": {"avg_ms": 20.0, "packet_loss_rate": 0.0},
        }
        after = {
            "run_id": "after", "phase": "after", "config_hash": "h",
            "aggregate": {"avg_ms": 35.0, "packet_loss_rate": 0.1},
        }
        result = compare_baseline(before, after)
        assert result["delta"]["avg_ms"]["delta_ms"] == 15.0
        assert result["same_configuration"] is True
        assert "does not by itself establish" in result["interpretation"]

    def test_feature_b_pair_keeps_the_two_labels_apart(self):
        diag = {
            "run_id": "d1", "simulated": False, "config": {"target": "127.0.0.1"},
            "config_hash": "h", "aggregate": {"avg_ms": 1.0, "packet_loss_rate": 0.0},
        }
        transfer = {
            "transfer_id": "t1", "scheduler_variant": "DB-DSP", "retransmit_policy": "RTX-CL",
            "total_chunks": 64, "delivered_chunks": 64, "integrity_ok": True,
        }
        pair = feature_b_side_by_side(diag, transfer)
        assert pair["diagnostics"]["data_kind"] == "real"
        assert pair["transfer"]["data_kind"] == "controlled"
        assert any("logical" in note.lower() for note in pair["notes"])


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    # Never persist a test run to whatever PostgreSQL is configured.
    app.dependency_overrides[get_optional_db] = lambda: None
    diagnostics_store.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    diagnostics_store.clear()


class TestDiagnosticsApi:
    def test_status_reports_real_versus_unsupported(self, client):
        r = client.get("/api/diagnostics/status", headers=READONLY)
        assert r.status_code == 200
        body = r.json()
        assert "real_measurements" in body
        # The replaced Linux capability is named as unsupported, with a reason.
        assert body["unsupported"]["kernel_emulation"]["supported"] is False
        assert body["unsupported"]["kernel_emulation"]["linux_reference"].startswith("https://")

    def test_scenarios_are_listed_and_flagged_simulated(self, client):
        r = client.get("/api/diagnostics/scenarios", headers=READONLY)
        assert r.status_code == 200
        body = r.json()
        assert {s["name"] for s in body["scenarios"]} >= {"unreachable", "packet_loss", "intermittent"}
        assert body["all_simulated"] is True

    def test_ping_loopback_is_a_real_measurement(self, client):
        r = client.post(
            "/api/diagnostics/ping",
            headers=READONLY,
            json={"target": "127.0.0.1", "count": 2, "timeout_s": 2},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["received"] == 2
        assert body["reachable"] is True

    def test_invalid_target_is_a_422_with_a_kind(self, client):
        r = client.post(
            "/api/diagnostics/ping",
            headers=READONLY,
            json={"target": "8.8.8.8 & del *", "count": 1},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["kind"] == "invalid_input"

    def test_run_bundle_is_stored_and_reportable(self, client):
        r = client.post(
            "/api/diagnostics/run",
            headers=OPERATOR,
            json={
                "target": "127.0.0.1",
                "count": 1,
                "timeout_s": 2,
                "include_traceroute": False,
                "include_adapters": False,
            },
        )
        assert r.status_code == 200
        run = r.json()
        assert run["data_kind"] == "real"
        assert "ping" in run["ok_sections"]
        run_id = run["run_id"]

        detail = client.get(f"/api/diagnostics/runs/{run_id}", headers=READONLY)
        assert detail.status_code == 200

        listing = client.get("/api/diagnostics/runs", headers=READONLY)
        assert listing.status_code == 200
        assert any(row["run_id"] == run_id for row in listing.json()["runs"])

        csv_export = client.get(f"/api/diagnostics/runs/{run_id}/export?format=csv", headers=READONLY)
        assert csv_export.status_code == 200
        assert csv_export.headers["content-type"].startswith("text/csv")

        bad_export = client.get(f"/api/diagnostics/runs/{run_id}/export?format=xml", headers=READONLY)
        assert bad_export.status_code == 422

    def test_unknown_run_is_404_not_a_fabricated_result(self, client):
        r = client.get("/api/diagnostics/runs/does-not-exist", headers=READONLY)
        assert r.status_code == 404

    def test_fault_scenario_is_marked_simulated(self, client):
        r = client.post(
            "/api/diagnostics/faults",
            headers=OPERATOR,
            json={"scenario": "intermittent", "seed": 9, "count": 6, "repetitions": 2, "period": 6},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["data_kind"] == "simulated"
        assert body["transition_count"] >= 1

    def test_bad_scenario_is_422(self, client):
        r = client.post("/api/diagnostics/faults", headers=OPERATOR, json={"scenario": "nope"})
        assert r.status_code == 422

    def test_benchmark_run_measures(self, client):
        r = client.post(
            "/api/diagnostics/benchmarks",
            headers=OPERATOR,
            json={"target": "127.0.0.1", "count": 2, "timeout_s": 2, "repetitions": 2},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["aggregate"]["received"] == 4
        assert body["config_hash"]

    def test_compare_labels_each_run(self, client):
        real = client.post(
            "/api/diagnostics/benchmarks",
            headers=OPERATOR,
            json={"target": "127.0.0.1", "count": 1, "timeout_s": 2, "repetitions": 1},
        ).json()
        sim = client.post(
            "/api/diagnostics/faults",
            headers=OPERATOR,
            json={"scenario": "packet_loss", "seed": 4, "count": 4, "repetitions": 1},
        ).json()
        r = client.post(
            "/api/diagnostics/runs/compare",
            headers=READONLY,
            json={"run_ids": [real["run_id"], sim["run_id"]]},
        )
        assert r.status_code == 200
        assert set(r.json()["data_kinds"]) == {"real", "simulated"}

    def test_hysteresis_sweep_includes_ratio_one_and_a_baseline(self, client):
        r = client.post(
            "/api/diagnostics/hysteresis-sweep",
            headers=OPERATOR,
            json={"repetitions": 5, "total_intervals": 200},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["data_kind"] == "controlled"
        assert any(abs(p["ratio"] - 1.0) < 1e-9 for p in body["points"])
        assert "naive" in body
        assert "pareto_frontier" in body

    def test_scheduler_comparison_returns_labeled_rows(self, client):
        r = client.post(
            "/api/diagnostics/scheduler-comparison",
            headers=OPERATOR,
            json={
                "payload_bytes": 16000,
                "repetitions": 5,
                "variants": ["DB-DSP", "RR"],
                "retransmit_policies": ["RTX-CL"],
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["data_kind"] == "controlled"
        assert len(body["rows"]) == 2

    def test_unknown_variant_is_refused(self, client):
        r = client.post(
            "/api/diagnostics/scheduler-comparison",
            headers=OPERATOR,
            json={"variants": ["NOT-A-VARIANT"]},
        )
        assert r.status_code == 422
        assert "valid variants" in json.dumps(r.json())

    def test_feature_a_capture_and_compare(self, client):
        before = client.post(
            "/api/diagnostics/integrations/feature-a/capture",
            headers=OPERATOR,
            json={"phase": "before", "target": "127.0.0.1", "count": 1, "timeout_s": 2},
        )
        assert before.status_code == 201
        assert before.json()["phase"] == "before"

        after = client.post(
            "/api/diagnostics/integrations/feature-a/capture",
            headers=OPERATOR,
            json={"phase": "after", "target": "127.0.0.1", "count": 1, "timeout_s": 2},
        )
        assert after.status_code == 201

        comparison = client.post(
            "/api/diagnostics/integrations/feature-a/compare",
            headers=READONLY,
            json={"before_run_id": before.json()["run_id"], "after_run_id": after.json()["run_id"]},
        )
        assert comparison.status_code == 200
        assert "delta" in comparison.json()

    def test_feature_b_pair_requires_a_stored_run(self, client):
        missing = client.post(
            "/api/diagnostics/integrations/feature-b",
            headers=READONLY,
            json={"diagnostics_run_id": "nope", "transfer": {"transfer_id": "t"}},
        )
        assert missing.status_code == 404

        run = client.post(
            "/api/diagnostics/run",
            headers=OPERATOR,
            json={"target": "127.0.0.1", "count": 1, "timeout_s": 2,
                  "include_traceroute": False, "include_adapters": False},
        ).json()
        paired = client.post(
            "/api/diagnostics/integrations/feature-b",
            headers=READONLY,
            json={
                "diagnostics_run_id": run["run_id"],
                "transfer": {"transfer_id": "t1", "total_chunks": 8, "integrity_ok": True},
            },
        )
        assert paired.status_code == 200
        assert paired.json()["transfer"]["data_kind"] == "controlled"

    def test_operator_routes_reject_readonly(self, client):
        r = client.post(
            "/api/diagnostics/run",
            headers=READONLY,
            json={"target": "127.0.0.1", "include_adapters": False, "include_traceroute": False},
        )
        assert r.status_code == 403
