"""
tests/test_labs.py — networking labs: routing, TCP congestion, shaping, TCP/UDP,
and the hysteresis → routing → transport integration.

The simulations are deterministic, so most assertions are exact. The one real
socket test uses loopback with small parameters.
"""

import pytest
from fastapi.testclient import TestClient

from netrescue.labs import integration, routing, shaping, tcp_cc, tcp_udp
from netrescue.labs.routing import Graph, GraphError, dijkstra, example_graph, k_shortest_paths


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_dijkstra_example_route_and_cost():
    r = dijkstra(example_graph(), "A", "F")
    assert r.path == ["A", "B", "D", "F"]
    assert r.cost == 6
    assert r.links == ["A-B", "B-D", "D-F"]
    assert r.visited[0] == "A" and r.visited[-1] == "F"
    assert r.trace[0].node == "A" and r.trace[0].distance == 0


def test_dijkstra_is_deterministic_with_ties():
    g = Graph()
    for n in "SABT":
        g.add_node(n)
    g.add_link("S", "A", 1)
    g.add_link("S", "B", 1)
    g.add_link("A", "T", 1)
    g.add_link("B", "T", 1)
    assert dijkstra(g, "S", "T").path == ["S", "A", "T"]   # ties break by node name
    assert dijkstra(g, "S", "T").path == dijkstra(g, "S", "T").path


def test_unreachable_destination_in_disconnected_graph():
    g = Graph()
    for n in "ABC":
        g.add_node(n)
    g.add_link("A", "B", 1)
    r = dijkstra(g, "A", "C")
    assert not r.reachable and r.path == [] and r.cost is None
    assert r.distances["C"] is None


@pytest.mark.parametrize("bad", [0, -1, float("inf"), float("nan"), "3", True])
def test_invalid_link_cost_is_rejected(bad):
    g = Graph()
    g.add_node("A"); g.add_node("B")
    with pytest.raises(GraphError):
        g.add_link("A", "B", bad)


def test_graph_edit_validation():
    g = example_graph()
    with pytest.raises(GraphError):
        g.add_node("A")                      # duplicate
    with pytest.raises(GraphError):
        g.add_link("A", "A", 1)              # self loop
    with pytest.raises(GraphError):
        g.add_link("A", "B", 1)              # duplicate link
    with pytest.raises(GraphError):
        g.add_link("A", "Z", 1)              # unknown node
    with pytest.raises(GraphError):
        dijkstra(g, "A", "nope")
    g.remove_node("D")
    assert "B-D" not in g.links and "D-F" not in g.links
    assert dijkstra(g, "A", "F").path == ["A", "B", "E", "F"]


def test_link_failure_reroutes_and_compares():
    out = routing.compare_routes(example_graph(), "A", "F",
                                 [{"kind": "link_down", "a": "B", "b": "D"}])
    assert out["before"]["route"]["path"] == ["A", "B", "D", "F"]
    assert out["after"]["route"]["path"] == ["A", "B", "E", "D", "F"]
    assert out["rerouted"] is True
    assert out["cost_delta"] == 1
    assert out["before"]["graph"]["links"] != out["after"]["graph"]["links"]


def test_cost_change_can_leave_route_unchanged():
    out = routing.compare_routes(example_graph(), "A", "F",
                                 [{"kind": "cost_change", "a": "A", "b": "C", "cost": 50}])
    assert out["rerouted"] is False and out["cost_delta"] == 0


def test_failure_that_disconnects_target():
    out = routing.compare_routes(example_graph(), "A", "F", [
        {"kind": "link_down", "a": "D", "b": "F"}, {"kind": "link_down", "a": "E", "b": "F"}])
    assert out["after"]["route"]["reachable"] is False
    assert "unreachable" in out["summary"]


def test_compare_does_not_mutate_input_graph():
    g = example_graph()
    routing.compare_routes(g, "A", "F", [{"kind": "link_down", "a": "B", "b": "D"}])
    assert g.links["B-D"].up is True


def test_k_shortest_paths_ordered_loopless_unique():
    paths = k_shortest_paths(example_graph(), "A", "F", 4)
    costs = [p["cost"] for p in paths]
    assert costs == sorted(costs) and costs[0] == 6
    assert len({tuple(p["path"]) for p in paths}) == len(paths)
    assert all(len(set(p["path"])) == len(p["path"]) for p in paths)
    assert paths[0]["path"] == dijkstra(example_graph(), "A", "F").path


# ---------------------------------------------------------------------------
# TCP congestion control
# ---------------------------------------------------------------------------


def _sim(**kw):
    return tcp_cc.simulate_tcp(tcp_cc.TcpParams(**kw))


def test_slow_start_roughly_doubles_each_rtt():
    r = _sim(rtt_ms=100, bandwidth_mbps=100, buffer_packets=1000, duration_s=2)
    series = r["cwnd_series"]
    assert series[0][3] == "slow_start" and series[0][1] == 1
    at = lambda t: max(row[1] for row in series if row[0] <= t)
    assert 2 <= at(0.15) <= 3
    assert 4 <= at(0.25) <= 6
    assert 8 <= at(0.35) <= 12


def test_congestion_avoidance_grows_about_one_segment_per_rtt():
    r = _sim(rtt_ms=100, bandwidth_mbps=100, buffer_packets=5000, duration_s=3,
             initial_ssthresh=8)
    ca = [row for row in r["cwnd_series"] if row[3] == "congestion_avoidance"]
    assert ca and all(row[1] >= 8 for row in ca)
    span = ca[-1][0] - ca[0][0]
    growth = ca[-1][1] - ca[0][1]
    assert 0.6 * (span / 0.1) <= growth <= 1.4 * (span / 0.1)


def test_loss_triggers_fast_retransmit_and_halves_ssthresh():
    r = _sim(loss_rate=0.01, seed=3, duration_s=15)
    s = r["summary"]
    assert s["fast_retransmits"] > 0 and s["retransmissions"] > 0
    assert any(e["type"] == "fast_retransmit" for e in r["events"])
    phases = {row[3] for row in r["cwnd_series"]}
    assert {"slow_start", "congestion_avoidance", "fast_recovery"} <= phases


def test_heavy_loss_causes_timeouts_and_cwnd_collapse():
    r = _sim(loss_rate=0.1, seed=5, duration_s=15)
    assert r["summary"]["timeouts"] > 0
    assert any(e["type"] == "timeout" for e in r["events"])
    assert min(row[1] for row in r["cwnd_series"][5:]) == 1


def test_buffer_overflow_produces_queue_drops_without_random_loss():
    r = _sim(loss_rate=0.0, buffer_packets=10, duration_s=10)
    assert r["summary"]["queue_drops"] > 0 and r["summary"]["random_drops"] == 0
    assert r["summary"]["retransmissions"] > 0


def test_goodput_never_exceeds_capacity_and_is_near_it_without_loss():
    r = _sim(bandwidth_mbps=5, rtt_ms=40, buffer_packets=60, duration_s=30)
    g = r["summary"]["goodput_mbps"]
    assert g <= 5.0 and g > 3.5


def test_higher_rtt_and_loss_reduce_goodput():
    base = _sim(rtt_ms=40, loss_rate=0.005, seed=2)["summary"]["goodput_mbps"]
    slow = _sim(rtt_ms=200, loss_rate=0.005, seed=2)["summary"]["goodput_mbps"]
    lossy = _sim(rtt_ms=40, loss_rate=0.04, seed=2)["summary"]["goodput_mbps"]
    assert slow < base and lossy < base


def test_tcp_simulation_is_reproducible_and_seed_sensitive():
    a = _sim(loss_rate=0.02, seed=9)
    assert a["summary"] == _sim(loss_rate=0.02, seed=9)["summary"]
    assert a["summary"] != _sim(loss_rate=0.02, seed=10)["summary"]


def test_transfer_completes_and_all_bytes_acked():
    r = _sim(transfer_bytes=500_000, loss_rate=0.01, seed=4, include_handshake=True)
    s = r["summary"]
    assert s["completed"] is True and s["bytes_acked"] == 500_000
    assert s["completion_time_s"] > 0.08            # at least handshake + one RTT


@pytest.mark.parametrize("kw", [{"variant": "cubic"}, {"loss_rate": 0.9}, {"rtt_ms": 0},
                                {"bandwidth_mbps": 0}, {"duration_s": 9999}])
def test_tcp_invalid_parameters_rejected(kw):
    with pytest.raises(tcp_cc.SimulationError):
        _sim(**kw)


def test_compare_scenarios_table():
    out = tcp_cc.compare_scenarios([
        ("low rtt", tcp_cc.TcpParams(rtt_ms=20)), ("high rtt", tcp_cc.TcpParams(rtt_ms=300))])
    assert [r["label"] for r in out["table"]] == ["low rtt", "high rtt"]
    with pytest.raises(tcp_cc.SimulationError):
        tcp_cc.compare_scenarios([])


# ---------------------------------------------------------------------------
# Traffic shaping
# ---------------------------------------------------------------------------


def _shape(**kw):
    return shaping.compare_shapers(shaping.ShapingParams(**kw))


def test_steady_traffic_below_rate_is_untouched_by_token_bucket():
    r = _shape(pattern="steady", arrival_rate_bps=500_000, rate_bps=1_000_000)
    tb, lb = r["token_bucket"]["summary"], r["leaky_bucket"]["summary"]
    assert tb["dropped_packets"] == 0 and lb["dropped_packets"] == 0
    assert tb["mean_delay_ms"] == 0                       # tokens always available
    assert lb["mean_delay_ms"] == pytest.approx(8.0, abs=0.01)   # 1000 B at 1 Mbit/s


def test_burst_token_bucket_passes_burst_leaky_bucket_smooths_it():
    r = _shape(pattern="burst", rate_bps=1_000_000, bucket_bytes=8_000, queue_packets=50,
               burst_packets=30, duration_s=2)
    tb, lb = r["token_bucket"]["summary"], r["leaky_bucket"]["summary"]
    assert tb["mean_delay_ms"] < lb["mean_delay_ms"]
    assert tb["peak_output_bps_100ms"] > lb["peak_output_bps_100ms"]
    assert lb["peak_output_bps_100ms"] <= 1_000_000 * 1.12


def test_overload_drops_packets_and_output_is_limited_to_rate():
    r = _shape(pattern="overload", rate_bps=1_000_000, queue_packets=10, duration_s=5)
    for alg in ("token_bucket", "leaky_bucket"):
        s = r[alg]["summary"]
        assert s["dropped_packets"] > 0
        assert s["delivered_packets"] + s["dropped_packets"] == s["offered_packets"]
        assert s["throughput_bps"] <= 1_000_000 * 1.15
        assert s["peak_queue_packets"] <= 10


def test_shaping_reproducible_and_seeded():
    a = _shape(pattern="poisson", seed=1)
    assert a["token_bucket"]["summary"] == _shape(pattern="poisson", seed=1)["token_bucket"]["summary"]
    assert a["workload"]["packets"] != _shape(pattern="poisson", seed=2)["workload"]["packets"]


def test_departures_are_causal_and_ordered():
    r = _shape(pattern="burst")
    for alg in ("token_bucket", "leaky_bucket"):
        deps = r[alg]["departures"]
        assert all(d >= a for a, d in deps)
        assert [d for _, d in deps] == sorted(d for _, d in deps)


@pytest.mark.parametrize("kw", [{"bucket_bytes": 500, "packet_bytes": 1000},
                                {"pattern": "nope"}, {"queue_packets": 0},
                                {"rate_bps": 1}])
def test_shaping_invalid_parameters(kw):
    with pytest.raises(shaping.ShapingError):
        _shape(**kw)


def test_shaping_workload_size_limit():
    with pytest.raises(shaping.ShapingError):
        _shape(pattern="steady", arrival_rate_bps=1e9, duration_s=60)


# ---------------------------------------------------------------------------
# TCP vs UDP
# ---------------------------------------------------------------------------


def test_simulated_comparison_is_labelled_and_consistent():
    r = tcp_udp.run_simulated(tcp_udp.SimParams(loss_rate=0.02, seed=1))
    assert r["mode"] == "simulated" and r["kind"] == "simulation"
    assert r["tcp"]["completed"] and r["tcp"]["delivered_ratio"] == 1.0
    assert r["udp"]["delivered_ratio"] < 1.0                   # no recovery for lost datagrams
    assert r["udp"]["packets_delivered"] + r["udp"]["queue_drops"] + r["udp"]["random_drops"] \
        == r["udp"]["packets_sent"]
    assert not any("always faster" in f or "always better" in f for f in r["findings"])


def test_simulated_no_loss_udp_delivers_everything():
    r = tcp_udp.run_simulated(tcp_udp.SimParams(loss_rate=0.0, udp_send_rate_mbps=5, bandwidth_mbps=10))
    assert r["udp"]["complete"] is True


def test_simulated_invalid_params():
    with pytest.raises(tcp_udp.LabError):
        tcp_udp.run_simulated(tcp_udp.SimParams(udp_send_rate_mbps=0))


def test_real_loopback_experiment_small():
    r = tcp_udp.run_real_loopback(tcp_udp.RealParams(
        ping_count=20, bulk_bytes=200_000, udp_datagrams=200))
    assert r["mode"] == "real_loopback" and r["kind"] == "measurement"
    assert r["tcp"]["errors"] == [] and r["udp"]["errors"] == []
    assert r["tcp"]["latency"]["n"] == 20
    assert r["tcp"]["bulk"]["bytes_delivered"] == 200_000
    assert r["tcp"]["connect_ms"] > 0 and r["udp"]["connect_ms"] == 0
    assert 0 < r["udp"]["bulk"]["delivered_ratio"] <= 1.0
    assert r["limitations"] and r["method"]


def test_real_loopback_rejects_concurrent_runs_and_bad_params():
    assert tcp_udp._real_lock.acquire(blocking=False)
    try:
        with pytest.raises(tcp_udp.LabBusy):
            tcp_udp.run_real_loopback(tcp_udp.RealParams(ping_count=10, bulk_bytes=100_000,
                                                          udp_datagrams=100))
    finally:
        tcp_udp._real_lock.release()
    with pytest.raises(tcp_udp.LabError):
        tcp_udp.run_real_loopback(tcp_udp.RealParams(ping_count=1))


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def test_integration_outage_reroutes_and_recovers():
    r = integration.run_integration(integration.IntegrationParams())
    states = [t["state"] for t in r["timeline"]]
    assert states == ["HEALTHY", "SUSPECT", "FAILED", "HEALTHY"]
    assert r["timeline"][1]["path"] == ["A", "B", "E", "D", "F"]       # penalty reroutes
    assert r["timeline"][-1]["path"] == ["A", "B", "D", "F"]           # recovered
    base, degraded, after = r["phases"]
    assert degraded["metrics"]["completion_time_s"] > base["metrics"]["completion_time_s"]
    assert after["path"] == ["A", "B", "E", "D", "F"]
    assert after["metrics"]["completion_time_s"] < degraded["metrics"]["completion_time_s"]
    assert r["csv"].splitlines()[0].startswith("phase,")
    assert len(r["csv"].splitlines()) == 4


def test_integration_uses_existing_state_machine_semantics():
    # Two misses (< f_thresh) never leave HEALTHY, so the route must not change.
    beats = [False, True, True, False, False, False]
    r = integration.run_integration(integration.IntegrationParams(heartbeats=beats))
    assert r["states"] == ["HEALTHY"] * len(beats)
    assert len(r["timeline"]) == 1
    assert r["phases"][2]["path"] == r["phases"][0]["path"]
    assert any("did not trigger" in n for n in r["outcome"]["notes"])


def test_integration_hysteresis_suppresses_flapping():
    beats = [True, True, False] * 10
    r = integration.run_integration(integration.IntegrationParams(heartbeats=beats))
    assert r["outcome"]["route_changes_hysteresis"] == 0
    assert r["outcome"]["heartbeat_toggles_naive"] > 10


def test_integration_unreachable_when_no_alternative():
    g = Graph()
    for n in "ABC":
        g.add_node(n)
    g.add_link("A", "B", 1)
    g.add_link("B", "C", 1)
    r = integration.run_integration(integration.IntegrationParams(
        graph=g, source="A", target="C", monitored_link="B-C"))
    assert r["phases"][2]["reachable"] is False
    assert r["phases"][2]["metrics"] is None


def test_integration_validation():
    for kw in ({"monitored_link": "X-Y"}, {"source": "A", "target": "A"},
               {"f_thresh": 5, "n": 3}, {"transfer_bytes": 1}):
        with pytest.raises(integration.IntegrationError):
            integration.run_integration(integration.IntegrationParams(**kw))


def test_integration_is_reproducible():
    a = integration.run_integration(integration.IntegrationParams())
    b = integration.run_integration(integration.IntegrationParams())
    assert a["phases"] == b["phases"] and a["csv"] == b["csv"]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

READONLY = {"X-NetRescue-Principal": "t", "X-NetRescue-Role": "readonly"}
OPERATOR = {"X-NetRescue-Principal": "t", "X-NetRescue-Role": "operator"}


@pytest.fixture(scope="module")
def client():
    from apps.api.main import app
    with TestClient(app) as c:
        yield c


def test_api_info_and_example(client):
    info = client.get("/api/labs/info", headers=READONLY).json()
    assert {m["id"] for m in info["modules"]} == {"routing", "tcp", "shaping", "tcp-udp", "integration"}
    assert info["boundaries"] and info["tcp_variants"] == ["reno"]
    ex = client.get("/api/labs/routing/example", headers=READONLY).json()
    assert ex["source"] == "A" and len(ex["graph"]["nodes"]) == 6


def test_api_post_requires_operator(client):
    r = client.post("/api/labs/tcp/simulate", json={}, headers=READONLY)
    assert r.status_code == 403


def test_api_routing_solve_and_whatif(client):
    g = client.get("/api/labs/routing/example", headers=READONLY).json()["graph"]
    r = client.post("/api/labs/routing/solve",
                    json={"graph": g, "source": "A", "target": "F"}, headers=OPERATOR)
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "simulation" and body["route"]["path"] == ["A", "B", "D", "F"]
    w = client.post("/api/labs/routing/whatif", headers=OPERATOR, json={
        "graph": g, "source": "A", "target": "F",
        "changes": [{"kind": "link_down", "a": "B", "b": "D"}]}).json()
    assert w["rerouted"] and w["after"]["route"]["cost"] == 7


def test_api_routing_errors(client):
    g = client.get("/api/labs/routing/example", headers=READONLY).json()["graph"]
    bad_dst = client.post("/api/labs/routing/solve", headers=OPERATOR,
                          json={"graph": g, "source": "A", "target": "ZZ"})
    assert bad_dst.status_code == 422 and "unknown destination" in bad_dst.json()["detail"]
    g["links"][0]["cost"] = -3
    assert client.post("/api/labs/routing/solve", headers=OPERATOR,
                       json={"graph": g, "source": "A", "target": "F"}).status_code == 422
    bad_kind = client.post("/api/labs/routing/whatif", headers=OPERATOR, json={
        "graph": {"nodes": ["A", "B"], "links": [{"a": "A", "b": "B", "cost": 1}]},
        "source": "A", "target": "B", "changes": [{"kind": "explode", "a": "A", "b": "B"}]})
    assert bad_kind.status_code == 422


def test_api_tcp_simulate_and_validation(client):
    ok = client.post("/api/labs/tcp/simulate", headers=OPERATOR,
                     json={"rtt_ms": 50, "loss_rate": 0.01, "duration_s": 5, "seed": 3})
    assert ok.status_code == 200
    body = ok.json()
    assert body["kind"] == "simulation" and body["assumptions"] and body["cwnd_series"]
    assert body["summary"]["variant"] == "reno"
    assert client.post("/api/labs/tcp/simulate", headers=OPERATOR,
                       json={"variant": "bbr"}).status_code == 422
    assert client.post("/api/labs/tcp/simulate", headers=OPERATOR,
                       json={"loss_rate": 2}).status_code == 422
    cmp_ = client.post("/api/labs/tcp/compare", headers=OPERATOR, json={"scenarios": [
        {"label": "a", "rtt_ms": 20, "duration_s": 3}, {"label": "b", "rtt_ms": 200, "duration_s": 3}]})
    assert cmp_.status_code == 200 and len(cmp_.json()["table"]) == 2


def test_api_shaping(client):
    r = client.post("/api/labs/shaping/simulate", headers=OPERATOR, json={"pattern": "burst"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "simulation"
    assert body["token_bucket"]["summary"]["offered_packets"] == \
        body["leaky_bucket"]["summary"]["offered_packets"]
    bad = client.post("/api/labs/shaping/simulate", headers=OPERATOR,
                      json={"bucket_bytes": 10, "packet_bytes": 1000})
    assert bad.status_code == 422


def test_api_tcp_udp_modes_are_labelled(client):
    sim = client.post("/api/labs/tcp-udp/simulate", headers=OPERATOR, json={}).json()
    assert sim["mode"] == "simulated" and sim["label"].startswith("SIMULATED")
    real = client.post("/api/labs/tcp-udp/real", headers=OPERATOR, json={
        "ping_count": 10, "bulk_bytes": 100_000, "udp_datagrams": 100})
    assert real.status_code == 200
    rb = real.json()
    assert rb["mode"] == "real_loopback" and rb["kind"] == "measurement"
    assert rb["label"].startswith("REAL")
    assert client.post("/api/labs/tcp-udp/real", headers=READONLY, json={}).status_code == 403
    assert client.post("/api/labs/tcp-udp/real", headers=OPERATOR,
                       json={"ping_count": 1}).status_code == 422


def test_api_integration(client):
    r = client.post("/api/labs/integration/run", headers=OPERATOR, json={})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "simulation" and len(body["phases"]) == 3 and body["csv"]
    assert body["boundaries"]
    bad = client.post("/api/labs/integration/run", headers=OPERATOR,
                      json={"monitored_link": "Q-R"})
    assert bad.status_code == 422


def test_oversized_shaping_workload_is_refused_before_it_is_generated():
    import time
    t0 = time.perf_counter()
    with pytest.raises(shaping.ShapingError, match="would have about"):
        _shape(pattern="steady", arrival_rate_bps=1e9, duration_s=120, packet_bytes=1)
    with pytest.raises(shaping.ShapingError, match="would have about"):
        _shape(pattern="burst", burst_packets=5000, burst_interval_s=0.01, duration_s=120)
    assert time.perf_counter() - t0 < 1.0        # a 15-million-entry list would take far longer
