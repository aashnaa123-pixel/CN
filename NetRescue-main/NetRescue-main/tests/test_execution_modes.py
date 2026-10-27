"""
tests/test_execution_modes.py — REAL vs SIMULATION is established by the backend.

The registry's claims are checked against the implementing source: a REAL
operation's code must contain an OS/network primitive, and a SIMULATION
operation's code must contain none. API responses must carry the verified
`execution` block, and a caller cannot influence it.
"""

import pytest
from fastapi.testclient import TestClient

from netrescue.labs import execution

READONLY = {"X-NetRescue-Principal": "t", "X-NetRescue-Role": "readonly"}
OPERATOR = {"X-NetRescue-Principal": "t", "X-NetRescue-Role": "operator"}


@pytest.mark.parametrize("op_id", sorted(execution.REGISTRY))
def test_every_registered_claim_is_verified_against_the_source(op_id):
    ok, evidence = execution.verify(op_id)
    assert ok, evidence
    assert execution.describe(op_id)["mode"] == execution.REGISTRY[op_id].mode


def test_real_operations_declare_primitives_and_simulations_do_not():
    for op in execution.REGISTRY.values():
        if op.mode == execution.REAL:
            assert op.primitives, op.id
        else:
            assert not op.primitives and op.model, op.id


def test_a_false_claim_is_reported_unverified(monkeypatch):
    fake = execution.Operation("x.fake", "demo", "Fake", execution.REAL, "claims to be real",
                               "netrescue.labs.routing", ("socket.socket(",))
    monkeypatch.setitem(execution.REGISTRY, "x.fake", fake)
    execution.verify.cache_clear()
    d = execution.describe("x.fake")
    assert d["mode"] == execution.UNVERIFIED and d["declared_mode"] == execution.REAL
    # and the reverse: a simulation whose code opens sockets must not pass
    fake2 = execution.Operation("x.fake2", "demo", "Fake2", execution.SIMULATION, "claims simulation",
                                "netrescue.labs.tcp_udp", (), model="m")
    monkeypatch.setitem(execution.REGISTRY, "x.fake2", fake2)
    assert execution.describe("x.fake2")["mode"] == execution.UNVERIFIED
    execution.verify.cache_clear()


def test_feature_modes_are_derived():
    prof = execution.profile()
    assert prof["routing"]["mode"] == execution.SIMULATION
    assert prof["tcp"]["mode"] == execution.SIMULATION
    assert prof["shaping"]["mode"] == execution.SIMULATION
    assert prof["integration"]["mode"] == execution.SIMULATION
    assert prof["multipath"]["mode"] == execution.SIMULATION
    assert prof["tcp-udp"]["mode"] == execution.MIXED
    assert prof["diagnostics"]["mode"] == execution.MIXED
    assert prof["hysteresis"]["mode"] == execution.MIXED


@pytest.fixture(scope="module")
def client():
    from apps.api.main import app
    with TestClient(app) as c:
        yield c


def test_execution_endpoint(client):
    body = client.get("/api/labs/execution", headers=READONLY).json()
    assert set(body["features"]) >= {"routing", "tcp", "shaping", "tcp-udp", "integration",
                                     "hysteresis", "multipath", "diagnostics"}
    assert all(o["mode"] in ("REAL", "SIMULATION") for f in body["features"].values()
               for o in f["operations"])
    assert body["definitions"]["REAL"] and body["definitions"]["SIMULATION"]


def test_lab_responses_carry_backend_established_execution(client):
    r = client.post("/api/labs/tcp/simulate", headers=OPERATOR,
                    json={"duration_s": 3, "seed": 5}).json()
    assert r["execution"]["mode"] == "SIMULATION" and r["execution"]["seed"] == 5
    assert r["execution"]["model"] and r["execution"]["evidence"]
    real = client.post("/api/labs/tcp-udp/real", headers=OPERATOR, json={
        "ping_count": 10, "bulk_bytes": 100_000, "udp_datagrams": 100}).json()
    assert real["execution"]["mode"] == "REAL"
    assert "127.0.0.1" in real["execution"]["target"]
    assert real["execution"]["outcome"].startswith("completed")
    sim = client.post("/api/labs/tcp-udp/simulate", headers=OPERATOR, json={}).json()
    assert sim["execution"]["mode"] == "SIMULATION"


def test_caller_cannot_inject_an_execution_mode(client):
    r = client.post("/api/labs/tcp/simulate", headers=OPERATOR,
                    json={"duration_s": 2, "mode": "REAL", "execution": {"mode": "REAL"}}).json()
    assert r["execution"]["mode"] == "SIMULATION"


def test_existing_features_report_their_mode(client):
    d = client.post("/api/diagnostics/faults", headers=OPERATOR,
                    json={"scenario": "packet_loss", "seed": 3}).json()
    assert d["execution"]["mode"] == "SIMULATION" and d["execution"]["seed"] == 3
    net = client.get("/api/diagnostics/network", headers=READONLY).json()
    assert net["execution"]["mode"] == "REAL"
    st = client.get("/api/xfer/state", headers=READONLY).json()
    assert st["execution"]["mode"] == "SIMULATION"       # the paths are modelled
    assert any("not a socket" in x for x in st["execution"]["limitations"])
    cmp_ = client.post("/api/playground/hysteresis/compare", headers=OPERATOR, json={
        "link_id": "A-B", "sequence": [False, True, True, True, False, False, False, False]}).json()
    assert cmp_["execution"]["mode"] == "SIMULATION"
