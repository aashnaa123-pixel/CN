"""CORS allow-list parsing/behaviour and hosted public-demo authorisation."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from netrescue.core.cors import CorsConfigError, install_cors, parse_cors_origins
from netrescue.security.authz import Role
from netrescue.security.demo import is_demo_operator_route

PROD = "https://netrescue-1.onrender.com"


# -- parsing ----------------------------------------------------------------


def test_parse_normalises_and_dedupes():
    got = parse_cors_origins(f" {PROD}/ , HTTPS://NetRescue-1.onrender.com, http://localhost:3000 ,")
    assert got == [PROD, "http://localhost:3000"]


def test_parse_empty_is_empty():
    assert parse_cors_origins("") == []


@pytest.mark.parametrize(
    "bad",
    ["*", "https://*.onrender.com", "netrescue-1.onrender.com", "ftp://x.com",
     "https://x.com/app", "https://x.com?a=1", "https://user:pw@x.com", "https://x.com:99999"],
)
def test_parse_rejects_invalid(bad):
    with pytest.raises(CorsConfigError):
        parse_cors_origins(bad)


# -- middleware behaviour ---------------------------------------------------


def _client(origins, loopback=True):
    app = FastAPI()
    install_cors(app, origins, loopback)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return TestClient(app)


def test_production_origin_allowed():
    r = _client([PROD]).get("/ping", headers={"Origin": PROD})
    assert r.headers["access-control-allow-origin"] == PROD


def test_unapproved_origin_rejected():
    r = _client([PROD]).get("/ping", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in r.headers


def test_lookalike_origin_rejected():
    c = _client([PROD])
    for origin in (PROD + ".evil.com", "http://netrescue-1.onrender.com", "https://x" + PROD[8:]):
        assert "access-control-allow-origin" not in c.get("/ping", headers={"Origin": origin}).headers


@pytest.mark.parametrize("origin", ["http://localhost:3000", "http://127.0.0.1:5173", "http://[::1]:8080"])
def test_loopback_allowed_by_default(origin):
    r = _client([PROD]).get("/ping", headers={"Origin": origin})
    assert r.headers["access-control-allow-origin"] == origin


def test_loopback_can_be_disabled():
    r = _client([PROD], loopback=False).get("/ping", headers={"Origin": "http://localhost:3000"})
    assert "access-control-allow-origin" not in r.headers


def test_never_wildcard():
    r = _client([PROD]).get("/ping", headers={"Origin": PROD})
    assert r.headers["access-control-allow-origin"] != "*"


def test_preflight_allowed():
    r = _client([PROD]).options(
        "/ping",
        headers={
            "Origin": PROD,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-netrescue-role,x-netrescue-principal",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == PROD
    assert "POST" in r.headers["access-control-allow-methods"]


def test_preflight_rejected_for_unapproved_origin():
    r = _client([PROD]).options(
        "/ping",
        headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers


def test_download_headers_exposed():
    r = _client([PROD]).get("/ping", headers={"Origin": PROD})
    assert "X-NetRescue-Reconstructed-Sha256" in r.headers["access-control-expose-headers"]


# -- demo-mode authorisation -------------------------------------------------


def _req(method, path, headers):
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": method, "path": path, "headers": raw, "query_string": b""})


OP = {"X-NetRescue-Role": "operator"}


def test_demo_route_matching_is_by_segment():
    assert is_demo_operator_route("POST", "/api/labs/tcp/simulate")
    assert is_demo_operator_route("POST", "/api/labs/tcp-udp/simulate")
    assert not is_demo_operator_route("POST", "/api/labs/tcp-udp/real")
    assert not is_demo_operator_route("POST", "/api/labs/tcpevil")
    assert not is_demo_operator_route("GET", "/api/labs/routing/solve")
    assert not is_demo_operator_route("POST", "/api/diagnostics/run")
    assert not is_demo_operator_route("DELETE", "/api/diagnostics/runs")
    assert not is_demo_operator_route("POST", "/emulation/config")


@pytest.mark.parametrize("token", ["", "s3cret"])
def test_demo_off_never_grants_operator_without_proof(monkeypatch, token):
    from apps.api import deps

    monkeypatch.setattr(deps.settings, "public_demo", False)
    monkeypatch.setattr(deps.settings, "api_auth_token", token)
    p = deps.resolve_principal(_req("POST", "/api/labs/routing/solve", OP))
    # With no token configured the legacy loopback trust model still applies.
    assert p.role is (Role.OPERATOR if not token else Role.READONLY)


@pytest.mark.parametrize("token", ["", "s3cret"])
def test_demo_grants_operator_only_on_allowlist(monkeypatch, token):
    from apps.api import deps

    monkeypatch.setattr(deps.settings, "public_demo", True)
    monkeypatch.setattr(deps.settings, "api_auth_token", token)
    assert deps.resolve_principal(_req("POST", "/api/labs/routing/solve", OP)).role is Role.OPERATOR
    assert deps.resolve_principal(_req("POST", "/api/diagnostics/run", OP)).role is Role.READONLY
    assert deps.resolve_principal(_req("POST", "/api/labs/tcp-udp/real", OP)).role is Role.READONLY
    assert deps.resolve_principal(_req("POST", "/emulation/config", OP)).role is Role.READONLY


def test_demo_never_grants_node_role(monkeypatch):
    from apps.api import deps

    monkeypatch.setattr(deps.settings, "public_demo", True)
    monkeypatch.setattr(deps.settings, "api_auth_token", "s3cret")
    p = deps.resolve_principal(_req("POST", "/api/labs/routing/solve", {"X-NetRescue-Role": "node"}))
    assert p.role is Role.READONLY


def test_token_holder_keeps_full_operator_in_demo(monkeypatch):
    from apps.api import deps

    monkeypatch.setattr(deps.settings, "public_demo", True)
    monkeypatch.setattr(deps.settings, "api_auth_token", "s3cret")
    h = {**OP, "X-NetRescue-Token": "s3cret"}
    assert deps.resolve_principal(_req("POST", "/api/diagnostics/run", h)).role is Role.OPERATOR
    bad = {**OP, "X-NetRescue-Token": "nope"}
    assert deps.resolve_principal(_req("POST", "/api/diagnostics/run", bad)).role is Role.READONLY


# -- the deployed failure, end to end ----------------------------------------


def _app_client(monkeypatch, *, demo):
    """The real app, configured like the Render backend: token set, browser headers only."""
    from apps.api import deps
    from apps.api.main import app

    monkeypatch.setattr(deps.settings, "api_auth_token", "server-side-secret")
    monkeypatch.setattr(deps.settings, "public_demo", demo)
    return TestClient(app)


BROWSER = {"X-NetRescue-Role": "operator", "X-NetRescue-Principal": "labs"}


def test_hosted_writes_were_403_without_demo_mode(monkeypatch):
    """Reproduces the reported bug: a token-protected API rejects the tokenless browser."""
    r = _app_client(monkeypatch, demo=False).post("/api/labs/shaping/simulate", headers=BROWSER, json={})
    assert r.status_code == 403


def test_hosted_simulations_run_in_demo_mode(monkeypatch):
    c = _app_client(monkeypatch, demo=True)
    for path in ("/api/labs/shaping/simulate", "/api/labs/tcp/simulate", "/api/labs/tcp-udp/simulate",
                 "/api/diagnostics/hysteresis-sweep", "/api/traffic/run"):
        assert c.post(path, headers=BROWSER, json={}).status_code in (200, 201), path
    assert c.post("/api/playground/topology/example", headers=BROWSER).status_code == 200


def test_hosted_host_operations_stay_denied_in_demo_mode(monkeypatch):
    c = _app_client(monkeypatch, demo=True)
    assert c.post("/api/labs/tcp-udp/real", headers=BROWSER, json={}).status_code == 403
    assert c.post("/api/diagnostics/run", headers=BROWSER, json={}).status_code == 403
    assert c.delete("/api/diagnostics/runs", headers=BROWSER).status_code == 403
    assert c.post("/api/lab/topology", headers=BROWSER, json={}).status_code == 403


# -- hosted diagnostics: real probes gated, simulations bounded ---------------

DIAG = {"X-NetRescue-Role": "operator", "X-NetRescue-Principal": "diagnostics-lab"}
HOST_PROBES = [
    ("GET", "/api/diagnostics/network", None),
    ("POST", "/api/diagnostics/ping", {"target": "127.0.0.1"}),
    ("POST", "/api/diagnostics/traceroute", {"target": "127.0.0.1"}),
    ("POST", "/api/diagnostics/dns", {"name": "example.com"}),
]


@pytest.mark.parametrize("method,path,body", HOST_PROBES)
def test_demo_blocks_anonymous_host_probes(monkeypatch, method, path, body):
    """On a public demo these run on the hosting server, so a tokenless caller (even
    read-only) is refused before any probe starts, with the reason in the message."""
    c = _app_client(monkeypatch, demo=True)
    for headers in (DIAG, {}):
        r = c.request(method, path, headers=headers, json=body)
        assert r.status_code == 403, (path, headers)
        assert "your device" in r.json()["detail"]


def test_token_holder_can_reach_host_probes_in_demo(monkeypatch):
    c = _app_client(monkeypatch, demo=True)
    h = {**DIAG, "X-NetRescue-Token": "server-side-secret"}
    # An invalid target is rejected by input validation: it got past authorisation.
    r = c.post("/api/diagnostics/ping", headers=h, json={"target": "bad target; rm -rf /"})
    assert r.status_code != 403 and r.status_code < 500


def test_local_use_keeps_read_access_to_probes(monkeypatch):
    """Without public-demo mode (a local install) the probes stay available to read-only callers."""
    from apps.api import deps

    monkeypatch.setattr(deps.settings, "api_auth_token", "")
    monkeypatch.setattr(deps.settings, "public_demo", False)
    from apps.api.main import app

    r = TestClient(app).post("/api/diagnostics/ping", json={"target": "bad target; x"})
    assert r.status_code != 403


@pytest.mark.parametrize("body", [
    {"payload_bytes": 1_000_000},
    {"repetitions": 20},
    {"paths": [{"path_id": f"p{i}"} for i in range(9)]},
])
def test_demo_bounds_scheduler_comparison(monkeypatch, body):
    c = _app_client(monkeypatch, demo=True)
    r = c.post("/api/diagnostics/scheduler-comparison", headers=DIAG, json=body)
    assert r.status_code == 422
    assert "public demo" in r.json()["detail"]


def test_demo_allows_only_one_scheduler_comparison_at_a_time(monkeypatch):
    from apps.api.routers import diagnostics as diag

    class Busy:
        def locked(self):
            return True

    monkeypatch.setattr(diag, "_scheduler_comparison_lock", Busy())
    c = _app_client(monkeypatch, demo=True)
    r = c.post("/api/diagnostics/scheduler-comparison", headers=DIAG,
               json={"payload_bytes": 8192, "chunk_size_bytes": 4096, "repetitions": 5})
    assert r.status_code == 429


def test_demo_simulations_still_work(monkeypatch):
    c = _app_client(monkeypatch, demo=True)
    assert c.post("/api/diagnostics/faults", headers=DIAG, json={"scenario": "packet_loss"}).status_code == 201
    assert c.post("/api/diagnostics/hysteresis-sweep", headers=DIAG, json={}).status_code == 201
