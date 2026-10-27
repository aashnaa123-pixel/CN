"""
tests/test_api.py — the HTTP surface: authorisation, the Recovery Lab, the
Traffic Lab, and the database URL handling that decides whether PostgreSQL works
at all.

These run against the real FastAPI app through an in-process transport. They
start real forwarding nodes on real sockets; nothing here is mocked.
"""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from netrescue.core.db import normalise_database_url

OPERATOR = {"X-NetRescue-Principal": "operator", "X-NetRescue-Role": "operator"}
READONLY = {"X-NetRescue-Principal": "viewer", "X-NetRescue-Role": "readonly"}


# ---------------------------------------------------------------------------
# Database URL handling
# ---------------------------------------------------------------------------


class TestDatabaseUrl:
    def test_explicit_driver_is_untouched(self):
        url = "postgresql+psycopg://u:p@localhost:5432/netrescue"
        assert normalise_database_url(url) == url

    def test_bare_postgres_gets_psycopg3_pinned(self):
        # psycopg2 is not installed; leaving this bare would fail to import.
        got = normalise_database_url("postgresql://u:p@localhost:5432/db")
        assert got == "postgresql+psycopg://u:p@localhost:5432/db"

    def test_pgbouncer_param_is_dropped(self):
        # psycopg3 raises `invalid connection option "pgbouncer"` otherwise.
        got = normalise_database_url("postgresql://u:p@host:6543/postgres?pgbouncer=true")
        assert "pgbouncer" not in got
        assert got == "postgresql+psycopg://u:p@host:6543/postgres"

    def test_meaningful_params_are_preserved(self):
        got = normalise_database_url(
            "postgresql+psycopg://u:p@h:5432/db?sslmode=require&pgbouncer=true"
        )
        assert "sslmode=require" in got
        assert "pgbouncer" not in got

    def test_query_is_not_reordered_when_nothing_is_removed(self):
        url = "postgresql+psycopg://u:p@h:5432/db?b=2&a=1"
        assert normalise_database_url(url) == url

    def test_non_postgres_is_untouched(self):
        assert normalise_database_url("sqlite:///local.db") == "sqlite:///local.db"

    def test_garbage_does_not_raise(self):
        # A URL we cannot improve is returned as-is so the real error surfaces.
        assert isinstance(normalise_database_url("not a url at all"), str)


# ---------------------------------------------------------------------------
# Health and authorisation
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health(self):
        with TestClient(app) as c:
            r = c.get("/health")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"

    def test_openapi_lists_the_new_screens(self):
        with TestClient(app) as c:
            paths = c.get("/openapi.json").json()["paths"]
            for expected in (
                "/api/lab/topology",
                "/api/lab/failures",
                "/api/lab/deliver",
                "/api/traffic/compare",
                "/api/traffic/schedulers",
            ):
                assert expected in paths, f"{expected} is not routed"


class TestAuthorisation:
    """Writes need the operator role; reads do not."""

    def test_readonly_cannot_start_a_topology(self):
        with TestClient(app) as c:
            r = c.post("/api/lab/topology", json={"nodes": ["A", "B"]}, headers=READONLY)
            assert r.status_code == 403

    def test_readonly_cannot_inject_a_failure(self):
        with TestClient(app) as c:
            c.post("/api/lab/topology", json={"nodes": ["A", "B", "C", "D"],
                                              "layout": "ring"}, headers=OPERATOR)
            try:
                r = c.post("/api/lab/failures", json={"kind": "node", "target": "B"},
                           headers=READONLY)
                assert r.status_code == 403
            finally:
                c.delete("/api/lab/topology", headers=OPERATOR)

    def test_anonymous_defaults_to_readonly_and_cannot_write(self):
        with TestClient(app) as c:
            r = c.post("/api/lab/topology", json={"nodes": ["A", "B"]})
            assert r.status_code == 403, (
                "an unlabelled request must be safe by default, not privileged"
            )

    def test_readonly_can_read_the_schedulers(self):
        with TestClient(app) as c:
            assert c.get("/api/traffic/schedulers", headers=READONLY).status_code == 200


# ---------------------------------------------------------------------------
# Recovery Lab
# ---------------------------------------------------------------------------


class TestRecoveryLab:
    @pytest.fixture(autouse=True)
    def _teardown(self):
        yield
        with TestClient(app) as c:
            c.delete("/api/lab/topology", headers=OPERATOR)

    def test_idle_topology_is_an_honest_empty_state(self):
        with TestClient(app) as c:
            c.delete("/api/lab/topology", headers=OPERATOR)
            body = c.get("/api/lab/topology", headers=OPERATOR).json()
            assert body["running"] is False
            assert body["nodes"] == []
            assert body["message"]

    def test_start_and_deliver(self):
        with TestClient(app) as c:
            r = c.post("/api/lab/topology",
                       json={"nodes": ["A", "B", "C"], "layout": "line"},
                       headers=OPERATOR)
            assert r.status_code == 200
            assert r.json()["topology"]["running"] is True

            d = c.post("/api/lab/deliver",
                       json={"source": "A", "destination": "C", "body": "hi"},
                       headers=OPERATOR).json()
            assert d["delivered"] is True
            assert d["path"] == ["A", "B", "C"]

    def test_invalid_topology_is_rejected_with_a_reason(self):
        with TestClient(app) as c:
            assert c.post("/api/lab/topology", json={"nodes": []},
                          headers=OPERATOR).status_code == 422
            bad = c.post("/api/lab/topology",
                         json={"nodes": ["A", "A"], "layout": "line"},
                         headers=OPERATOR)
            assert bad.status_code == 422
            assert "unique" in bad.json()["detail"].lower()

    def test_delivery_operations_require_a_running_topology(self):
        with TestClient(app) as c:
            c.delete("/api/lab/topology", headers=OPERATOR)
            r = c.post("/api/lab/deliver",
                       json={"source": "A", "destination": "B"},
                       headers=OPERATOR)
            assert r.status_code == 409
            assert "topology" in r.json()["detail"].lower()

    def test_recovery_around_a_failure_is_reported_from_a_real_delivery(self):
        """Kill B in a ring; A must still reach C the long way round."""
        with TestClient(app) as c:
            c.post("/api/lab/topology",
                   json={"nodes": ["A", "B", "C", "D"], "layout": "ring"},
                   headers=OPERATOR)

            before = c.post("/api/lab/deliver",
                            json={"source": "A", "destination": "C"},
                            headers=OPERATOR).json()
            assert before["path"] == ["A", "B", "C"]

            f = c.post("/api/lab/failures",
                       json={"kind": "node", "target": "B"}, headers=OPERATOR)
            assert f.status_code == 200

            after = c.post("/api/lab/deliver",
                           json={"source": "A", "destination": "C"},
                           headers=OPERATOR).json()
            assert after["delivered"] is True
            assert after["path"] == ["A", "D", "C"]

    def test_a_dead_destination_is_reported_unreachable_not_delivered(self):
        with TestClient(app) as c:
            c.post("/api/lab/topology",
                   json={"nodes": ["A", "B", "C", "D"], "layout": "ring"},
                   headers=OPERATOR)
            c.post("/api/lab/failures", json={"kind": "node", "target": "B"},
                   headers=OPERATOR)

            r = c.post("/api/lab/deliver",
                       json={"source": "A", "destination": "B"},
                       headers=OPERATOR).json()
            assert r["delivered"] is False
            assert r["reason"]

    def test_unknown_failure_target_is_404_with_the_known_set(self):
        with TestClient(app) as c:
            c.post("/api/lab/topology", json={"nodes": ["A", "B"]},
                   headers=OPERATOR)
            r = c.post("/api/lab/failures",
                       json={"kind": "node", "target": "ZZZ"}, headers=OPERATOR)
            assert r.status_code == 404
            assert "ZZZ" in r.json()["detail"]

    def test_clear_failures_restores_delivery(self):
        with TestClient(app) as c:
            c.post("/api/lab/topology",
                   json={"nodes": ["A", "B", "C"], "layout": "line"},
                   headers=OPERATOR)
            c.post("/api/lab/failures", json={"kind": "node", "target": "B"},
                   headers=OPERATOR)
            assert c.post("/api/lab/deliver",
                          json={"source": "A", "destination": "C"},
                          headers=OPERATOR).json()["delivered"] is False

            c.post("/api/lab/failures/clear", headers=OPERATOR)
            assert c.post("/api/lab/deliver",
                          json={"source": "A", "destination": "C"},
                          headers=OPERATOR).json()["delivered"] is True

    def test_routes_endpoint_shows_alternate_paths(self):
        with TestClient(app) as c:
            c.post("/api/lab/topology",
                   json={"nodes": ["A", "B", "C", "D"], "layout": "ring"},
                   headers=OPERATOR)
            c.post("/api/lab/failures", json={"kind": "node", "target": "B"},
                   headers=OPERATOR)
            routes = c.get("/api/lab/routes", headers=OPERATOR).json()["routes"]
            assert routes["A"]["C"]["path"] == ["A", "D", "C"]
            assert routes["A"]["B"]["status"] == "UNREACHABLE"


# ---------------------------------------------------------------------------
# Traffic Lab
# ---------------------------------------------------------------------------


class TestTrafficLab:
    def test_schedulers_are_discoverable(self):
        with TestClient(app) as c:
            body = c.get("/api/traffic/schedulers", headers=READONLY).json()
            ids = [s["id"] for s in body["schedulers"]]
            assert ids == ["fifo", "priority"]
            assert len(body["traffic_classes"]) == 2
            assert "not a measurement" in body["disclaimer"]

    def test_compare_returns_both_schedulers_with_the_tradeoff(self):
        with TestClient(app) as c:
            r = c.post("/api/traffic/compare", json={
                "workload": {"critical_rate_hz": 40, "bulk_rate_hz": 200,
                             "duration_ms": 400},
                "link": {"rate_bps": 1_000_000},
            }, headers=OPERATOR)
            assert r.status_code == 200
            body = r.json()

            assert [x["scheduler"] for x in body["results"]] == ["fifo", "priority"]
            assert body["congested"] is True
            for result in body["results"]:
                assert result["simulated"] is True
                assert set(result["classes"]) == {"critical", "bulk"}

            by = {x["scheduler"]: x["classes"] for x in body["results"]}
            f_lat = by["fifo"]["critical"]["latency_median_ms"]
            p_lat = by["priority"]["critical"]["latency_median_ms"]
            assert p_lat < f_lat, "priority must lower critical latency when congested"

            text = " ".join(body["findings"]).lower()
            assert "no hard real-time guarantee" in text

    def test_results_are_identical_arrivals(self):
        with TestClient(app) as c:
            body = c.post("/api/traffic/compare", json={
                "workload": {"duration_ms": 300}, "link": {"rate_bps": 1_000_000},
            }, headers=OPERATOR).json()
            a, b = body["results"]
            assert a["workload"] == b["workload"]
            for cls in ("critical", "bulk"):
                assert (a["classes"][cls]["packets_offered"]
                        == b["classes"][cls]["packets_offered"])

    def test_single_run(self):
        with TestClient(app) as c:
            r = c.post("/api/traffic/run", json={
                "scheduler": "fifo",
                "workload": {"duration_ms": 200},
                "link": {"rate_bps": 1_000_000},
            }, headers=OPERATOR)
            assert r.status_code == 200
            body = r.json()
            assert body["result"]["scheduler"] == "fifo"
            assert body["result"]["simulated"] is True
            assert "not a measurement" in body["caveat"]

    def test_unknown_scheduler_is_rejected(self):
        with TestClient(app) as c:
            r = c.post("/api/traffic/run", json={"scheduler": "magic"},
                       headers=OPERATOR)
            assert r.status_code == 422
            assert "fifo" in r.json()["detail"]

    def test_run_requires_the_operator_role(self):
        with TestClient(app) as c:
            assert c.post("/api/traffic/compare", json={},
                         headers=READONLY).status_code == 403

    def test_absurd_parameters_are_rejected(self):
        with TestClient(app) as c:
            bad = c.post("/api/traffic/compare", json={
                "workload": {"duration_ms": -5}, "link": {"rate_bps": 0},
            }, headers=OPERATOR)
            assert bad.status_code == 422
