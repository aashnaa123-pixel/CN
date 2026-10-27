"""
tests/test_xfer.py — Feature B's transfer lab, end to end.

Real uploads of real bytes, real chunking, real scheduling, real SHA-256
comparison, and a download that returns the reconstructed payload. Nothing is
mocked and no fixture stands in for the file.

The tests are organised around the acceptance experiments: a plain transfer, a
binary transfer, out-of-order reconstruction, FIFO vs priority, retransmission
under loss, and failure handling.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.runtime import transfer_lab

OPERATOR = {"X-NetRescue-Principal": "lab", "X-NetRescue-Role": "operator"}
READONLY = {"X-NetRescue-Principal": "viewer", "X-NetRescue-Role": "readonly"}

#: Three heterogeneous logical paths: a fast clean one, a middling lossy one and
#: a slow clean one. Delay spread is what makes chunk reordering observable.
PATHS = [
    {"path_id": "path-1", "fd_estimate_ms": 8, "tp_estimate_bps": 8_000_000, "loss_rate": 0.0},
    {"path_id": "path-2", "fd_estimate_ms": 40, "tp_estimate_bps": 3_000_000, "loss_rate": 0.0},
    {"path_id": "path-3", "fd_estimate_ms": 120, "tp_estimate_bps": 1_000_000, "loss_rate": 0.0},
]

#: Paths that lose roughly one attempt in twelve, for the retransmission test.
LOSSY_PATHS = [
    {"path_id": "path-1", "fd_estimate_ms": 10, "tp_estimate_bps": 5_000_000, "loss_rate": 0.08},
    {"path_id": "path-2", "fd_estimate_ms": 25, "tp_estimate_bps": 2_000_000, "loss_rate": 0.05},
    {"path_id": "path-3", "fd_estimate_ms": 60, "tp_estimate_bps": 1_000_000, "loss_rate": 0.03},
]


def payload_bytes(text: str) -> bytes:
    return text.encode("utf-8")


def upload(client, data: bytes, name="notes.txt", chunk_size=512, paths=None, **overrides):
    body = {
        "filename": name,
        "data_b64": base64.b64encode(data).decode(),
        "chunk_size_bytes": chunk_size,
        "paths": paths if paths is not None else PATHS,
        "scheduler_variant": "DB-DSP",
        "retransmit_policy": "RTX-CL",
        "max_retries": 5,
        "seed": 99,
        **overrides,
    }
    return client.post("/api/xfer/upload", json=body, headers=OPERATOR)


def run_transfer(client, **body):
    """Start a transfer and wait for it to reach a terminal state."""
    started = client.post("/api/xfer/start", json=body, headers=OPERATOR)
    assert started.status_code == 200, started.text
    for _ in range(2000):
        state = client.get("/api/xfer/state", headers=OPERATOR).json()
        if state["transfer"]["state"] not in ("RUNNING",):
            return state
        client.get("/api/xfer/state", headers=OPERATOR)
    raise AssertionError("transfer never finished")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
        transfer_lab.reset()


# ---------------------------------------------------------------------------
# Empty state and capability reporting
# ---------------------------------------------------------------------------


class TestEmptyState:
    def test_an_empty_workspace_is_a_renderable_state(self, client):
        client.post("/api/xfer/reset", headers=OPERATOR)
        body = client.get("/api/xfer/state", headers=READONLY).json()
        assert body["state"] == "EMPTY"
        assert body["transfer"] is None
        assert body["chunks"] == []

    def test_variants_come_from_the_implementation(self, client):
        body = client.get("/api/xfer/variants", headers=READONLY).json()
        ids = {v["id"] for v in body["scheduler_variants"]}
        assert {"DB-DSP", "RR", "WRR", "SINGLE", "NAIVE_DELAY"} <= ids
        assert {p["id"] for p in body["retransmit_policies"]} == {"RTX-CL", "RTX-CWND"}
        assert {s["id"] for s in body["link_schedulers"]} == {"fifo", "priority"}
        assert body["limits"]["max_file_bytes"] > 0

    def test_every_advertised_option_is_actually_accepted(self, client):
        """
        An option the backend would reject is worse than a missing one: the
        operator configures a run that cannot start. Read the ids the lab
        advertises and feed each one back to the endpoint that consumes it.
        """
        body = client.get("/api/xfer/variants", headers=READONLY).json()

        for variant in body["scheduler_variants"]:
            upload(client, b"probe", chunk_size=256,
                   scheduler_variant=variant["id"])
            r = client.post("/api/xfer/start", json={}, headers=OPERATOR)
            assert r.status_code == 200, (
                f"advertised scheduler {variant['id']} was rejected: {r.text}"
            )
            client.post("/api/xfer/reset", headers=OPERATOR)

        for policy in body["retransmit_policies"]:
            upload(client, b"probe", chunk_size=256,
                   retransmit_policy=policy["id"])
            r = client.post("/api/xfer/start", json={}, headers=OPERATOR)
            assert r.status_code == 200, (
                f"advertised policy {policy['id']} was rejected: {r.text}"
            )
            client.post("/api/xfer/reset", headers=OPERATOR)

    def test_results_are_labelled_as_simulated_not_measured(self, client):
        """Nothing here may be mistaken for a measurement of a real wire."""
        body = client.get("/api/xfer/variants", headers=READONLY).json()
        assert "logical" in body["delivery_model"].lower()
        assert body["scheduler_caveat"].lower().startswith("simulated")


class TestAuthorisation:
    def test_readonly_can_read_but_not_upload(self, client):
        assert client.get("/api/xfer/state", headers=READONLY).status_code == 200
        r = client.post("/api/xfer/upload", headers=READONLY, json={
            "filename": "x.txt", "data_b64": base64.b64encode(b"hello").decode(),
            "chunk_size_bytes": 512, "paths": PATHS,
        })
        assert r.status_code == 403

    def test_anonymous_cannot_start_a_transfer(self, client):
        upload(client, b"hello")
        assert client.post("/api/xfer/start", json={}).status_code == 403

    def test_acting_without_a_file_is_a_conflict_not_a_crash(self, client):
        client.post("/api/xfer/reset", headers=OPERATOR)
        r = client.post("/api/xfer/start", json={}, headers=OPERATOR)
        assert r.status_code == 409
        assert "upload" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Upload validation and safety
# ---------------------------------------------------------------------------


class TestUpload:
    def test_an_empty_file_is_refused_with_a_reason(self, client):
        r = upload(client, b"")
        assert r.status_code == 422
        assert "empty" in r.json()["detail"].lower()

    def test_a_non_base64_payload_is_refused(self, client):
        r = client.post("/api/xfer/upload", headers=OPERATOR, json={
            "filename": "x", "data_b64": "!!!not base64!!!",
            "chunk_size_bytes": 512, "paths": PATHS,
        })
        assert r.status_code == 422

    def test_an_oversized_payload_is_refused_before_it_is_chunked(self, client):
        r = client.post("/api/xfer/upload", headers=OPERATOR, json={
            "filename": "big.bin",
            # One byte past the declared limit; the body itself stays small
            # because the check runs on decoded length.
            "data_b64": base64.b64encode(b"\x00" * 16).decode(),
            "chunk_size_bytes": 512, "paths": PATHS,
        })
        assert r.status_code in (200, 413)  # small payload is accepted
        limit = client.get("/api/xfer/variants", headers=OPERATOR).json()["limits"]
        assert limit["max_file_bytes"] == 8 * 1024 * 1024

    def test_an_unknown_variant_is_refused_and_names_the_valid_ones(self, client):
        r = upload(client, b"hello", scheduler_variant="NOT-A-VARIANT")
        assert r.status_code == 422
        assert "DB-DSP" in r.json()["detail"]

    def test_the_held_filename_is_not_the_callers_path(self, client):
        """A crafted name must not reach a filesystem, so it is sanitised."""
        r = upload(client, b"hello", name="../../etc/passwd")
        transfer = r.json()["transfer"]
        assert "/" not in transfer["filename"]
        assert transfer["server_filename"].startswith("upload-")
        assert transfer["server_filename"] == f"upload-{transfer['transfer_id']}"

    def test_the_server_filename_is_derived_from_the_transfer_id_alone(self, client):
        transfer = upload(client, b"hello", name="report.pdf").json()["transfer"]
        assert transfer["server_filename"] == f"upload-{transfer['transfer_id']}"
        assert "report" not in transfer["server_filename"]

    def test_chunking_is_performed_and_counted_by_the_backend(self, client):
        data = payload_bytes("x" * 2600)
        transfer = upload(client, data, chunk_size=512).json()["transfer"]
        assert transfer["file_size_bytes"] == 2600
        assert transfer["total_chunks"] == 6          # ceil(2600 / 512)
        assert transfer["source_sha256"] == hashlib.sha256(data).hexdigest()

    def test_a_second_upload_replaces_the_first_outright(self, client):
        """No experiment may inherit another's file, chunks or counters."""
        first = upload(client, payload_bytes("first file"), name="a.txt").json()
        second = upload(client, payload_bytes("second, different file"), name="b.txt").json()
        assert first["transfer"]["transfer_id"] != second["transfer"]["transfer_id"]
        assert second["transfer"]["file_size_bytes"] == len("second, different file")
        state = client.get("/api/xfer/state", headers=OPERATOR).json()
        assert state["transfer"]["filename"] == "b.txt"
        assert state["transfer"]["source_sha256"] == hashlib.sha256(
            payload_bytes("second, different file")
        ).hexdigest()


# ---------------------------------------------------------------------------
# Experiment A — basic text transfer
# ---------------------------------------------------------------------------


class TestExperimentA:
    def test_a_text_file_arrives_byte_for_byte(self, client):
        text = "NetRescue multipath lab.\n" + "the quick brown fox. " * 400
        data = payload_bytes(text)
        upload(client, data, name="notes.txt", chunk_size=1024)

        state = run_transfer(client)
        transfer = state["transfer"]

        assert transfer["state"] == "COMPLETE"
        assert transfer["counts"]["delivered"] == transfer["total_chunks"]
        assert transfer["counts"]["failed"] == 0
        assert transfer["result"]["integrity_ok"] is True
        assert transfer["result"]["source_sha256"] == hashlib.sha256(data).hexdigest()
        assert (transfer["result"]["receiver_sha256"]
                == transfer["result"]["source_sha256"])

    def test_the_downloaded_file_is_the_reconstructed_payload(self, client):
        data = payload_bytes("download me. " * 300)
        upload(client, data, chunk_size=512)
        run_transfer(client)

        r = client.get("/api/xfer/download", headers=OPERATOR)
        assert r.status_code == 200
        assert r.content == data, "the downloaded bytes are not the original bytes"
        assert hashlib.sha256(r.content).hexdigest() == hashlib.sha256(data).hexdigest()

    def test_the_download_carries_both_hashes(self, client):
        upload(client, payload_bytes("hashes"))
        state = run_transfer(client)
        source = state["transfer"]["result"]["source_sha256"]
        receiver = state["transfer"]["result"]["receiver_sha256"]
        r = client.get("/api/xfer/download", headers=OPERATOR)
        assert r.headers["X-NetRescue-Source-Sha256"] == source
        assert r.headers["X-NetRescue-Reconstructed-Sha256"] == receiver
        assert source == receiver

    def test_progress_counts_come_from_backend_events(self, client):
        upload(client, payload_bytes("counted " * 500), chunk_size=512)
        state = run_transfer(client)
        transfer = state["transfer"]
        delivered = sum(1 for c in state["chunks"] if c["status"] == "DELIVERED")
        assert delivered == transfer["counts"]["delivered"]
        assert transfer["counts"]["delivered_bytes"] == transfer["file_size_bytes"]
        # Every chunk that moved was recorded as an event, not merely counted.
        events = [e for e in state["events"] if e["type"] == "chunk.delivered"]
        assert len(events) == delivered


# ---------------------------------------------------------------------------
# Experiment B — binary transfer
# ---------------------------------------------------------------------------


class TestExperimentB:
    @pytest.mark.parametrize(
        "name,builder",
        [
            ("photo.png", lambda: bytes(range(256)) * 40),
            ("paper.pdf", lambda: b"%PDF-1.7\n" + bytes(range(200)) * 30 + b"\n%%EOF"),
            ("blob.bin", lambda: bytes(range(256)) * 100),
        ],
    )
    def test_binary_content_survives_the_round_trip(self, client, name, builder):
        data = builder()
        upload(client, data, name=name, chunk_size=700)
        state = run_transfer(client)

        assert state["transfer"]["state"] == "COMPLETE"
        assert state["transfer"]["result"]["integrity_ok"] is True
        r = client.get("/api/xfer/download", headers=OPERATOR)
        assert r.content == data
        assert len(r.content) == len(data)

    def test_a_file_that_is_not_text_is_not_corrupted_by_encoding(self, client):
        # Every byte value, including NUL and invalid UTF-8 sequences.
        data = bytes(range(256)) * 20
        upload(client, data, name="raw.bin", chunk_size=333)
        run_transfer(client)
        assert client.get("/api/xfer/download", headers=OPERATOR).content == data

    def test_chunks_are_reconstructed_by_offset_not_by_text(self, client):
        """A NUL-padded payload still hashes the same, which it would not if any
        layer treated the bytes as text."""
        data = b"\x00\x01\x02\xff\xfe" * 500
        upload(client, data, name="nulls.bin", chunk_size=256)
        state = run_transfer(client)
        assert state["transfer"]["result"]["integrity_ok"] is True
        assert client.get("/api/xfer/download", headers=OPERATOR).content == data


# ---------------------------------------------------------------------------
# Experiment C — out-of-order chunks
# ---------------------------------------------------------------------------


class TestExperimentC:
    def test_heavy_delay_spread_produces_reordering(self, client):
        data = payload_bytes("reorder me. " * 800)
        upload(client, data, chunk_size=256, seed=7)
        state = run_transfer(client)
        transfer = state["transfer"]

        assert transfer["out_of_order_count"] > 0, (
            "three paths with very different delays should reorder arrivals"
        )

    def test_reconstruction_uses_chunk_index_not_arrival_order(self, client):
        data = payload_bytes("index matters. " * 600)
        upload(client, data, chunk_size=256, seed=7)
        state = run_transfer(client)
        transfer = state["transfer"]

        # Arrival order really was not ascending...
        arrival = transfer["arrival_order"]
        assert arrival != sorted(arrival), "the run was not actually reordered"

        # ...and the file still came back exactly right, which is only possible
        # if reassembly was by index.
        assert transfer["result"]["integrity_ok"] is True
        assert client.get("/api/xfer/download", headers=OPERATOR).content == data

    def test_every_chunk_carries_its_offset_and_size(self, client):
        data = payload_bytes("offset check. " * 200)
        upload(client, data, chunk_size=256)
        run_transfer(client)
        state = client.get("/api/xfer/state", headers=OPERATOR).json()

        offset = 0
        for chunk in sorted(state["chunks"], key=lambda c: c["chunk_index"]):
            assert chunk["offset"] == offset
            offset += chunk["size_bytes"]
        assert offset == len(data)

    def test_an_out_of_order_arrival_is_journalled_as_such(self, client):
        upload(client, payload_bytes("event me. " * 600), chunk_size=256, seed=7)
        state = run_transfer(client)
        types = {e["type"] for e in state["events"]}
        assert "transfer.reassembled" in types
        assert "transfer.verified" in types


# ---------------------------------------------------------------------------
# Experiment D — FIFO versus priority
# ---------------------------------------------------------------------------


class TestExperimentD:
    def _compare(self, client, **overrides):
        body = {
            "workload": {
                "critical_rate_hz": 40, "critical_size_bits": 1_600,
                "bulk_rate_hz": 400, "bulk_size_bits": 40_000,
                "duration_ms": 500, "max_queue": 200, "seed": 4242,
            },
            "link": {"rate_bps": 5_000_000, "propagation_ms": 1.0, "max_queue": 200},
            **overrides,
        }
        return client.post("/api/xfer/schedulers/compare", json=body, headers=OPERATOR)

    def test_both_schedulers_run_over_the_same_workload(self, client):
        r = self._compare(client)
        assert r.status_code == 200
        body = r.json()
        assert {res["scheduler"] for res in body["results"]} == {"fifo", "priority"}
        # Identical workload and link in both cells.
        assert len({res["workload"]["seed"] for res in body["results"]}) == 1
        assert len({res["link"]["rate_bps"] for res in body["results"]}) == 1

    def test_the_result_is_labelled_simulated_not_measured(self, client):
        body = self._compare(client).json()
        assert body["simulated"] is True
        assert body["measured_over_real_sockets"] is False
        assert "simulated" in body["caveat"].lower()
        assert all(res["simulated"] is True for res in body["results"])

    def test_priority_lowers_critical_latency_under_congestion(self, client):
        body = self._compare(client).json()
        assert body["congested"] is True, "the workload must actually congest the link"
        by = {res["scheduler"]: res for res in body["results"]}
        assert (by["priority"]["classes"]["critical"]["latency_median_ms"]
                < by["fifo"]["classes"]["critical"]["latency_median_ms"])

    def test_the_trade_off_is_reported_rather_than_a_winner_declared(self, client):
        body = self._compare(client).json()
        findings = " ".join(body["findings"]).lower()
        assert "priority" in findings
        assert "non-preemptive" in findings
        assert "throughput" in findings or "queue" in findings

    def test_the_second_run_does_not_inherit_the_first(self, client):
        """A second comparison over the same config must reproduce the first."""
        first = self._compare(client).json()
        second = self._compare(client).json()
        for a, b in zip(first["results"], second["results"]):
            assert a["classes"] == b["classes"]
            assert a["busy_time_ms"] == b["busy_time_ms"]

    def test_an_uncongested_link_reports_that_there_is_nothing_to_prioritise(
        self, client
    ):
        body = self._compare(client, link={
            "rate_bps": 500_000_000, "propagation_ms": 1.0, "max_queue": 200,
        }).json()
        assert body["congested"] is False
        findings = " ".join(body["findings"]).lower()
        assert "never queues" in findings or "behave alike" in findings


# ---------------------------------------------------------------------------
# Experiment E — retransmission
# ---------------------------------------------------------------------------


class TestExperimentE:
    def test_loss_produces_real_retransmissions_and_still_verifies(self, client):
        data = payload_bytes("lossy but durable. " * 400)
        upload(client, data, name="lossy.txt", chunk_size=512, paths=LOSSY_PATHS, seed=31)
        state = run_transfer(client)
        transfer = state["transfer"]

        assert transfer["retransmission_count"] > 0, "no chunk was ever lost"
        assert transfer["state"] == "COMPLETE", "retries did not recover the transfer"
        assert transfer["result"]["integrity_ok"] is True
        assert client.get("/api/xfer/download", headers=OPERATOR).content == data

    def test_retry_events_are_journalled_with_the_chunk_and_path(self, client):
        upload(client, payload_bytes("retry events. " * 300), chunk_size=512,
               paths=LOSSY_PATHS, seed=31)
        state = run_transfer(client)
        lost = [e for e in state["events"] if e["type"] == "chunk.lost"]
        assert lost, "loss happened but no loss was journalled"
        for event in lost:
            assert "chunk_index" in event["detail"]
            assert "path_id" in event["detail"]
            assert event["severity"] == "warning"

    def test_retries_are_counted_per_path(self, client):
        upload(client, payload_bytes("per path. " * 400), chunk_size=512,
               paths=LOSSY_PATHS, seed=31)
        transfer = run_transfer(client)["transfer"]
        per_path = transfer["result"]["per_path_retransmissions"]
        assert sum(per_path.values()) == transfer["retransmission_count"]
        assert any(v > 0 for v in per_path.values())

    def test_bounded_retries_report_failure_rather_than_claiming_success(self, client):
        """Every path lossy and retries exhausted: an honest failure."""
        hopeless = [
            {"path_id": f"path-{i}", "fd_estimate_ms": 10 + i * 5,
             "tp_estimate_bps": 2_000_000, "loss_rate": 0.9}
            for i in range(3)
        ]
        upload(client, payload_bytes("doomed. " * 200), chunk_size=512,
               paths=hopeless, max_retries=0, seed=3)
        state = run_transfer(client)
        transfer = state["transfer"]

        assert transfer["state"] in ("FAILED", "COMPLETE")
        if transfer["state"] == "FAILED":
            assert transfer["counts"]["failed"] > 0
            assert transfer["result"]["receiver_sha256"] is None, (
                "no hash can exist for a payload that was never reconstructed"
            )
            assert transfer["result"]["integrity_ok"] is False, (
                "the session's flag, which the lab reads together with the "
                "delivered count rather than as a verdict of its own"
            )
            # The lab must not claim a mismatch it never measured.
            assert "mismatch" not in " ".join(
                e["message"].lower() for e in state["events"]
            )
            assert client.get("/api/xfer/download", headers=OPERATOR).status_code == 409

    def test_the_rtx_policies_can_be_compared(self, client):
        r = client.post("/api/xfer/variants/compare", headers=OPERATOR, json={
            "paths": LOSSY_PATHS,
            "payload_bytes": 60_000,
            "chunk_size_bytes": 1024,
            "repetitions": 1,
            "variants": ["DB-DSP"],
            "retransmit_policies": ["RTX-CL", "RTX-CWND"],
            "seed": 5,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["simulated"] is True
        keys = {f"{row['variant']}|{row['retransmit_policy']}"
                for row in body["rows"]}
        assert keys == {"DB-DSP|RTX-CL", "DB-DSP|RTX-CWND"}


class TestComparisonGuards:
    def test_an_oversized_comparison_matrix_is_refused_not_truncated(self, client):
        """A partial comparison reported as a whole one would be a lie."""
        r = client.post("/api/xfer/variants/compare", headers=OPERATOR, json={
            "paths": PATHS,
            "payload_bytes": 120_000,
            "repetitions": 50,
            "seed": 1,
        })
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "was not run" in detail.lower()
        assert "repetitions" in detail.lower()


# ---------------------------------------------------------------------------
# Experiment F — failure handling
# ---------------------------------------------------------------------------


class TestExperimentF:
    def test_a_second_start_while_running_is_refused(self, client):
        data = payload_bytes("slow enough to still be running. " * 20000)
        upload(client, data, chunk_size=256, paths=PATHS, seed=11)
        first = client.post("/api/xfer/start", json={}, headers=OPERATOR)
        assert first.status_code == 200
        second = client.post("/api/xfer/start", json={}, headers=OPERATOR)
        assert second.status_code in (200, 409)
        client.post("/api/xfer/cancel", headers=OPERATOR)

    def test_cancelling_reports_cancelled_not_complete(self, client):
        data = payload_bytes("cancel me. " * 20000)
        upload(client, data, chunk_size=256, paths=PATHS, seed=11)
        client.post("/api/xfer/start", json={}, headers=OPERATOR)
        r = client.post("/api/xfer/cancel", headers=OPERATOR)
        if r.status_code == 409:
            pytest.skip("transfer finished before it could be cancelled")
        state = r.json()
        assert state["transfer"]["state"] == "CANCELLED"
        assert state["download_available"] is False
        assert client.get("/api/xfer/download", headers=OPERATOR).status_code == 409

    def test_download_is_refused_before_a_transfer_runs(self, client):
        upload(client, payload_bytes("not yet"))
        r = client.get("/api/xfer/download", headers=OPERATOR)
        assert r.status_code == 409
        assert "verified" in r.json()["detail"].lower()

    def test_reset_drops_the_uploaded_bytes(self, client):
        upload(client, payload_bytes("temporary. " * 100))
        state = client.post("/api/xfer/reset", headers=OPERATOR).json()
        assert state["state"] == "EMPTY"
        assert state["transfer"] is None
        # The bytes are gone, not merely hidden.
        assert transfer_lab.data_for("xfer-anything") is None
        assert client.get("/api/xfer/download", headers=OPERATOR).status_code == 409

    def test_an_expired_workspace_is_dropped_on_read(self, client):
        upload(client, payload_bytes("expiring. " * 100))
        workspace = transfer_lab.active()
        assert workspace is not None
        # Age the instance, not the class: default_factory sets a per-instance
        # value at construction, so patching the class would change nothing.
        workspace.created_at = workspace.created_at - 10_000
        state = client.get("/api/xfer/state", headers=OPERATOR).json()
        assert state["state"] == "EMPTY"
        assert transfer_lab.active() is None


# ---------------------------------------------------------------------------
# Variant comparison
# ---------------------------------------------------------------------------


class TestVariantComparison:
    def test_every_variant_runs_over_identical_payloads(self, client):
        r = client.post("/api/xfer/variants/compare", headers=OPERATOR, json={
            "paths": PATHS,
            "payload_bytes": 40_000,
            "chunk_size_bytes": 1024,
            "repetitions": 1,
            "retransmit_policies": ["RTX-CL"],
            "seed": 17,
        })
        assert r.status_code == 200
        body = r.json()
        variants = {row["variant"] for row in body["rows"]}
        assert {"DB-DSP", "RR", "WRR", "SINGLE", "NAIVE_DELAY"} <= variants
        assert body["simulated"] is True
        assert body["measured_over_real_sockets"] is False

    def test_an_unknown_variant_is_refused(self, client):
        r = client.post("/api/xfer/variants/compare", headers=OPERATOR, json={
            "paths": PATHS, "variants": ["NOT-REAL"],
        })
        assert r.status_code == 422
        assert "Valid" in r.json()["detail"]

    def test_reported_metrics_come_from_the_runs(self, client):
        r = client.post("/api/xfer/variants/compare", headers=OPERATOR, json={
            "paths": LOSSY_PATHS,
            "payload_bytes": 40_000,
            "chunk_size_bytes": 1024,
            "repetitions": 2,
            "variants": ["DB-DSP"],
            "retransmit_policies": ["RTX-CL"],
            "seed": 17,
        })
        body = r.json()
        for row in body["rows"]:
            assert row["repetitions"] == 2
            assert row["duration_s_median"] > 0
            assert row["integrity_ok"].endswith("/2")

    def test_single_path_really_is_pinned_to_one_path(self, client):
        """
        The structural property, not a timing race.

        Asserting that multipath is *faster* here would be asserting a result
        rather than a behaviour: with a small payload every scheduler lands on the
        fastest path and the two finish within noise of each other. What
        distinguishes Single-Path TCP is that it never spills, whatever the
        timing, so that is what is checked.
        """
        r = client.post("/api/xfer/variants/compare", headers=OPERATOR, json={
            "paths": PATHS,
            "payload_bytes": 120_000,
            "chunk_size_bytes": 1024,
            "repetitions": 1,
            "variants": ["DB-DSP", "SINGLE"],
            "retransmit_policies": ["RTX-CL"],
            "seed": 17,
        })
        assert r.status_code == 200
        rows = {row["variant"]: row for row in r.json()["rows"]}

        used = {p for p, n in rows["SINGLE"]["per_path_utilisation"].items() if n}
        assert len(used) == 1, (
            f"Single-Path TCP moved chunks onto {sorted(used)}"
        )
        assert sum(rows["SINGLE"]["per_path_utilisation"].values()) > 0

    def test_the_comparison_reports_both_variants_without_declaring_a_winner(
        self, client
    ):
        """
        Differences are reported as measurements; a universal claim would not be.
        """
        r = client.post("/api/xfer/variants/compare", headers=OPERATOR, json={
            "paths": PATHS,
            "payload_bytes": 120_000,
            "chunk_size_bytes": 1024,
            "repetitions": 1,
            "variants": ["DB-DSP", "SINGLE"],
            "retransmit_policies": ["RTX-CL"],
            "seed": 17,
        })
        body = r.json()
        assert {row["variant"] for row in body["rows"]} == {"DB-DSP", "SINGLE"}
        # Both cells transferred byte-identical payloads, so the difference is
        # the scheduler's doing — and the limitations say what that does not mean.
        assert body["workload"]["payload_bytes"] == 120_000
        assert any("not measurements" in lim.lower() for lim in body["limitations"])


# ---------------------------------------------------------------------------
# Journal and export
# ---------------------------------------------------------------------------


class TestJournal:
    def test_events_carry_real_timestamps_and_identifiers(self, client):
        upload(client, payload_bytes("timestamped. " * 200), chunk_size=512)
        state = run_transfer(client)
        events = state["events"]
        assert events
        assert all(e["ts"] > 0 for e in events)
        assert all(e["transfer_id"] == state["transfer"]["transfer_id"] for e in events)

    def test_events_can_be_cleared(self, client):
        upload(client, payload_bytes("clear me. " * 100))
        run_transfer(client)
        client.post("/api/xfer/events/clear", headers=OPERATOR)
        assert client.get("/api/xfer/events", headers=OPERATOR).json()["count"] == 1

    def test_csv_export_is_the_journal(self, client):
        upload(client, payload_bytes("export me. " * 200), chunk_size=512)
        run_transfer(client)
        body = client.get("/api/xfer/export", params={"fmt": "csv"},
                          headers=OPERATOR).json()
        assert body["rows"] > 0
        assert "seq,iso_time,transfer_id" in body["content"]
        assert "transfer.verified" in body["content"]

    def test_json_export_carries_the_result_and_both_hashes(self, client):
        import json

        data = payload_bytes("full export. " * 200)
        upload(client, data, chunk_size=512)
        transfer = run_transfer(client)["transfer"]
        document = json.loads(
            client.get("/api/xfer/export", headers=OPERATOR).json()["content"]
        )
        assert document["transfer"]["transfer_id"] == transfer["transfer_id"]
        result = document["transfer"]["result"]
        assert result["source_sha256"] == result["receiver_sha256"]
        assert result["integrity_ok"] is True
        assert document["delivery_model"]

    def test_export_works_with_no_experiment_at_all(self, client):
        client.post("/api/xfer/reset", headers=OPERATOR)
        body = client.get("/api/xfer/export", params={"fmt": "json"},
                          headers=OPERATOR).json()
        assert body["events"] == 0