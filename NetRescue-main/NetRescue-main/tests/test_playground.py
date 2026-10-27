"""
tests/test_playground.py — Feature A's interactive workbench, end to end.

These start real forwarding nodes on real sockets. Nothing is mocked: a link
that is reported failed here is failed in the router that moves the packets, and
a delivery reported delivered here was acknowledged by the destination node.

The experiments are the acceptance tests from the specification — A through F —
run through the HTTP surface a browser uses, so the endpoints and the runtime are
exercised together rather than separately.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.runtime import playground_service
from netrescue.playground import PlaygroundTopology, validate_topology

OPERATOR = {"X-NetRescue-Principal": "operator", "X-NetRescue-Role": "operator"}
READONLY = {"X-NetRescue-Principal": "viewer", "X-NetRescue-Role": "readonly"}


EXAMPLE = {
    "nodes": [
        {"node_id": "A", "label": "Source A", "role": "source", "x": 90, "y": 200},
        {"node_id": "B", "label": "Router B", "role": "router", "x": 320, "y": 90},
        {"node_id": "C", "label": "Router C", "role": "router", "x": 320, "y": 310},
        {"node_id": "D", "label": "Router D", "role": "router", "x": 610, "y": 200},
        {"node_id": "E", "label": "Destination E", "role": "destination",
         "x": 880, "y": 200},
    ],
    "links": [
        {"node_a": "A", "node_b": "B"},
        {"node_a": "A", "node_b": "C"},
        {"node_a": "B", "node_b": "D"},
        {"node_a": "C", "node_b": "D"},
        {"node_a": "D", "node_b": "E"},
    ],
    "source": "A",
    "destination": "E",
}

# A → B → C → E. One path only: failing anything on it strands the destination.
CHAIN = {
    "nodes": [
        {"node_id": "A", "role": "source"},
        {"node_id": "B", "role": "router"},
        {"node_id": "C", "role": "destination"},
    ],
    "links": [
        {"node_a": "A", "node_b": "B"},
        {"node_a": "B", "node_b": "C"},
    ],
    "source": "A",
    "destination": "C",
}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
        c.post("/api/playground/teardown", headers=OPERATOR)
        playground_service.reset()


def spec(body):
    """The same shape the API accepts, as a PlaygroundTopology for unit checks."""
    from netrescue.playground import TopologyLinkSpec, TopologyNodeSpec

    return PlaygroundTopology(
        nodes=[
            TopologyNodeSpec(n["node_id"], n.get("label", n["node_id"]),
                             n.get("role", "router"), n.get("x", 0.0), n.get("y", 0.0))
            for n in body["nodes"]
        ],
        links=[
            TopologyLinkSpec(f"{min(l['node_a'], l['node_b'])}-{max(l['node_a'], l['node_b'])}",
                             l["node_a"], l["node_b"])
            for l in body["links"]
        ],
    )


def apply(client, **overrides):
    return client.post(
        "/api/playground/apply", headers=OPERATOR,
        json={"heartbeat_interval_s": 0.2, **overrides},
    )


def test_state_is_readable_before_anything_exists(client):
    """An empty workspace is a state to render, not an error."""
    client.post("/api/playground/topology/clear", headers=OPERATOR)
    body = client.get("/api/playground/state", headers=READONLY).json()
    assert body["running"] is False
    assert body["nodes"] == []
    assert body["validation"]["valid"] is False


def test_clearing_the_workspace_releases_the_running_sockets(client):
    """
    The stop must actually happen, not merely be scheduled.

    Leaving listeners open against a topology that no longer exists would let a
    packet cross a wire the operator has just deleted from the canvas.
    """
    client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
    assert apply(client).json()["running"] is True

    state = client.post("/api/playground/topology/clear", headers=OPERATOR).json()
    assert state["running"] is False
    assert state["nodes"] == []
    assert state["applied"] is None

    # And nothing is left forwarding: a delivery now has nowhere to go.
    r = client.post("/api/playground/test",
                    json={"source": "A", "destination": "E"}, headers=OPERATOR)
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Authorisation — same rules as the rest of the control plane
# ---------------------------------------------------------------------------


class TestAuthorisation:
    def test_readonly_can_read_the_state(self, client):
        assert client.get("/api/playground/state", headers=READONLY).status_code == 200

    def test_readonly_cannot_apply_a_topology(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        assert client.post("/api/playground/apply", json={},
                           headers=READONLY).status_code == 403

    def test_anonymous_cannot_fail_anything(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)
        assert client.post("/api/playground/nodes/B/fail").status_code == 403

    def test_testing_without_a_running_topology_is_a_conflict_not_a_failure(self, client):
        r = client.post("/api/playground/test", json={"source": "A", "destination": "E"},
                        headers=OPERATOR)
        assert r.status_code == 409
        assert "Apply" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_the_example_has_an_alternate_route(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        report = client.post("/api/playground/validate", json={},
                             headers=OPERATOR).json()["validation"]
        assert report["valid"] is True
        assert report["routable"] is True
        assert report["has_alternate"] is True
        assert report["primary_path"] == ["A", "B", "D", "E"]
        assert report["alternate_path"] == ["A", "C", "D", "E"]

    def test_a_duplicate_node_id_is_refused_with_the_id_named(self):
        body = dict(EXAMPLE, nodes=EXAMPLE["nodes"] + [EXAMPLE["nodes"][0]])
        report = validate_topology(spec(body), source="A", destination="E")
        assert report["valid"] is False
        assert any(e["code"] == "duplicate_node_id" and e["target"] == "A"
                   for e in report["errors"])

    def test_a_link_to_a_node_that_does_not_exist_is_refused(self):
        body = dict(EXAMPLE, nodes=[n for n in EXAMPLE["nodes"] if n["node_id"] != "B"])
        report = validate_topology(spec(body), source="A", destination="E")
        codes = {e["code"] for e in report["errors"]}
        assert "dangling_link" in codes

    def test_a_self_connection_is_refused(self):
        body = {"nodes": [EXAMPLE["nodes"][0]],
                "links": [{"node_a": "A", "node_b": "A"}]}
        report = validate_topology(spec(body), source="A", destination="A")
        assert any(e["code"] == "self_link" for e in report["errors"])

    def test_wiring_the_same_pair_twice_is_refused(self):
        body = dict(EXAMPLE,
                    links=EXAMPLE["links"] + [{"node_a": "B", "node_b": "A"}])
        report = validate_topology(spec(body), source="A", destination="E")
        assert any(e["code"] == "duplicate_link" for e in report["errors"])

    def test_a_topology_without_a_destination_is_invalid(self):
        body = dict(EXAMPLE,
                    nodes=[n for n in EXAMPLE["nodes"] if n["role"] != "destination"])
        report = validate_topology(spec(body), source="A")
        assert any(e["code"] == "no_destination" for e in report["errors"])

    def test_source_and_destination_must_differ(self):
        report = validate_topology(spec(EXAMPLE), source="A", destination="A")
        assert any(e["code"] == "same_endpoint" for e in report["errors"])

    def test_an_unusable_node_id_is_refused_with_the_allowed_shape(self):
        body = dict(EXAMPLE)
        body["nodes"] = [dict(n, node_id="A-B") if n["node_id"] == "B" else n
                         for n in body["nodes"]]
        report = validate_topology(spec(body), source="A", destination="E")
        assert any(e["code"] == "invalid_node_id" for e in report["errors"])

    def test_structurally_valid_but_unroutable_is_reported_as_such(self, client):
        """The distinction that matters: well-formed is not the same as usable."""
        body = dict(EXAMPLE)
        body["links"] = [{"node_a": "A", "node_b": "B"}, {"node_a": "C", "node_b": "D"}]
        client.put("/api/playground/topology", json=body, headers=OPERATOR)
        report = client.post("/api/playground/validate", json={},
                             headers=OPERATOR).json()["validation"]
        assert report["valid"] is True, "the graph is well formed"
        assert report["routable"] is False, "and still cannot carry a packet"
        assert report["primary_path"] is None

    def test_a_malformed_topology_cannot_be_applied_at_all(self, client):
        """A graph that cannot be started is refused; one that can be, is not."""
        body = dict(EXAMPLE, nodes=EXAMPLE["nodes"] + [EXAMPLE["nodes"][0]])
        client.put("/api/playground/topology", json=body, headers=OPERATOR)
        r = apply(client)
        assert r.status_code == 422
        assert "unique" in r.json()["detail"]["message"].lower()

    def test_an_unroutable_topology_applies_but_reports_itself_as_unusable(self, client):
        """
        Building a chain one link at a time is legitimate work, so the nodes
        start; what is refused is pretending the network can carry a packet.
        """
        body = dict(EXAMPLE, links=[{"node_a": "A", "node_b": "B"}])
        client.put("/api/playground/topology", json=body, headers=OPERATOR)
        state = apply(client).json()
        assert state["running"] is True
        assert state["validation"]["valid"] is True
        assert state["validation"]["routable"] is False
        assert state["route"] is None

        delivery = client.post("/api/playground/test",
                               json={"source": "A", "destination": "E"},
                               headers=OPERATOR).json()
        assert delivery["delivered"] is False
        assert "no route" in delivery["reason"].lower()


# ---------------------------------------------------------------------------
# Experiment A — basic connectivity
# ---------------------------------------------------------------------------


class TestExperimentA:
    def test_packet_reaches_the_destination_over_the_drawn_topology(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        assert apply(client).json()["running"] is True

        r = client.post("/api/playground/test",
                        json={"source": "A", "destination": "E"},
                        headers=OPERATOR).json()
        assert r["delivered"] is True, r.get("reason")
        assert r["path"] == ["A", "B", "D", "E"]

        state = client.get("/api/playground/state", headers=OPERATOR).json()
        assert state["route"] == ["A", "B", "D", "E"]
        assert state["packets"] == {"sent": 1, "delivered": 1, "failed": 0}
        # The links on the route are the ones marked as carrying it.
        in_route = {l["link_id"] for l in state["links"] if l["in_route"]}
        assert in_route == {"A-B", "B-D", "D-E"}

    def test_the_nodes_that_ran_are_the_nodes_that_were_drawn(self, client):
        """The canvas and the network must not be able to drift apart."""
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        state = apply(client).json()
        assert state["draft_is_running"] is True
        assert [n["node_id"] for n in state["nodes"]] == ["A", "B", "C", "D", "E"]
        assert all(n["port"] for n in state["nodes"]), "each node holds a real port"
        assert {l["link_id"] for l in state["links"]} == {
            "A-B", "A-C", "B-D", "C-D", "D-E"
        }


# ---------------------------------------------------------------------------
# Experiment B — fail a link
# ---------------------------------------------------------------------------


class TestExperimentB:
    def test_failing_a_link_routes_around_it_and_delivery_still_succeeds(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)

        first = client.post("/api/playground/test",
                            json={"source": "A", "destination": "E"},
                            headers=OPERATOR).json()
        assert first["path"] == ["A", "B", "D", "E"]

        state = client.post("/api/playground/links/A-B/fail", headers=OPERATOR).json()
        cut = next(l for l in state["links"] if l["link_id"] == "A-B")
        assert cut["status"] == "DOWN"
        assert cut["enabled"] is False
        assert cut["in_route"] is False
        assert any(i["code"] == "link_down" for i in cut["issues"])

        second = client.post("/api/playground/test",
                             json={"source": "A", "destination": "E"},
                             headers=OPERATOR).json()
        assert second["delivered"] is True, second.get("reason")
        assert second["path"] == ["A", "C", "D", "E"], "routed the long way round"
        assert "A-B" not in second["path"]

        state = client.get("/api/playground/state", headers=OPERATOR).json()
        assert state["previous_route"] == ["A", "B", "D", "E"]
        assert state["route_changes"] == 1

    def test_a_failed_link_is_not_used_even_though_its_nodes_are_alive(self, client):
        """A red line is not a failure. The routing tables must actually avoid it."""
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)
        client.post("/api/playground/links/B-D/fail", headers=OPERATOR)

        r = client.post("/api/playground/test",
                        json={"source": "A", "destination": "E"},
                        headers=OPERATOR).json()
        assert r["delivered"] is True
        assert r["path"] == ["A", "C", "D", "E"]

        # B's own table for E must not point across the failed link either.
        state = client.get("/api/playground/state", headers=OPERATOR).json()
        b_entry = next(n for n in state["nodes"] if n["node_id"] == "B")["routes"]["E"]
        assert "B-D" not in _links_in(b_entry["path"])


def _links_in(path):
    return [f"{path[i]}-{path[i + 1]}" for i in range(len(path) - 1)]


# ---------------------------------------------------------------------------
# Experiment C — fail a node
# ---------------------------------------------------------------------------


class TestExperimentC:
    def test_a_failed_forwarder_cannot_forward_and_traffic_routes_around_it(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)

        before = client.post("/api/playground/test",
                             json={"source": "A", "destination": "E"},
                             headers=OPERATOR).json()
        assert before["path"] == ["A", "B", "D", "E"]

        state = client.post("/api/playground/nodes/B/fail", headers=OPERATOR).json()
        b = next(n for n in state["nodes"] if n["node_id"] == "B")
        assert b["failed"] is True
        assert b["running"] is False, "its listener is closed, not flagged"

        after = client.post("/api/playground/test",
                            json={"source": "A", "destination": "E"},
                            headers=OPERATOR).json()
        assert after["delivered"] is True, after.get("reason")
        assert after["path"] == ["A", "C", "D", "E"]
        assert "B" not in after["path"]

    def test_incident_links_of_a_failed_node_are_reported_unavailable(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)
        state = client.post("/api/playground/nodes/B/fail", headers=OPERATOR).json()
        for link_id in ("A-B", "B-D"):
            link = next(l for l in state["links"] if l["link_id"] == link_id)
            assert link["enabled"] is False
            assert any(i["code"] == "endpoint_down" for i in link["issues"])


# ---------------------------------------------------------------------------
# Experiment D — no alternate route
# ---------------------------------------------------------------------------


class TestExperimentD:
    def test_delivery_fails_and_says_why_when_the_only_link_is_cut(self, client):
        client.put("/api/playground/topology", json=CHAIN, headers=OPERATOR)
        apply(client)

        ok = client.post("/api/playground/test",
                         json={"source": "A", "destination": "C"},
                         headers=OPERATOR).json()
        assert ok["delivered"] is True
        assert ok["path"] == ["A", "B", "C"]

        client.post("/api/playground/links/A-B/fail", headers=OPERATOR)
        after = client.post("/api/playground/test",
                            json={"source": "A", "destination": "C"},
                            headers=OPERATOR).json()
        assert after["delivered"] is False
        assert "no route" in after["reason"].lower()

        state = client.get("/api/playground/state", headers=OPERATOR).json()
        assert state["route"] is None
        assert state["packets"] == {"sent": 2, "delivered": 1, "failed": 1}
        assert any(e["code"] == "destination_unreachable"
                   for e in state["validation"]["errors"])

    def test_delivery_fails_when_the_only_forwarder_is_stopped(self, client):
        client.put("/api/playground/topology", json=CHAIN, headers=OPERATOR)
        apply(client)
        client.post("/api/playground/nodes/B/fail", headers=OPERATOR)

        after = client.post("/api/playground/test",
                            json={"source": "A", "destination": "C"},
                            headers=OPERATOR).json()
        assert after["delivered"] is False
        assert after["reason"]

    def test_the_timeline_records_the_unreachable_finding(self, client):
        client.put("/api/playground/topology", json=CHAIN, headers=OPERATOR)
        apply(client)
        client.post("/api/playground/nodes/B/fail", headers=OPERATOR)
        client.post("/api/playground/test", json={"source": "A", "destination": "C"},
                    headers=OPERATOR)
        events = client.get("/api/playground/events", headers=OPERATOR).json()["events"]
        assert any("Delivery failed" in e["message"] for e in events)


# ---------------------------------------------------------------------------
# Experiment E — restore
# ---------------------------------------------------------------------------


class TestExperimentE:
    def test_restoring_a_link_reinstates_it_and_delivery_uses_it(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)
        client.post("/api/playground/links/A-B/fail", headers=OPERATOR)
        client.post("/api/playground/test", json={"source": "A", "destination": "E"},
                    headers=OPERATOR)

        state = client.post("/api/playground/links/A-B/restore", headers=OPERATOR).json()
        link = next(l for l in state["links"] if l["link_id"] == "A-B")
        assert link["enabled"] is True
        assert link["status"] == "UP"

        r = client.post("/api/playground/test",
                        json={"source": "A", "destination": "E"},
                        headers=OPERATOR).json()
        assert r["delivered"] is True
        assert r["path"] == ["A", "B", "D", "E"], "the short path is preferred again"

    def test_restoring_a_node_reopens_its_listener(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)
        client.post("/api/playground/nodes/B/fail", headers=OPERATOR)

        state = client.post("/api/playground/nodes/B/restore", headers=OPERATOR).json()
        b = next(n for n in state["nodes"] if n["node_id"] == "B")
        assert b["failed"] is False
        assert b["running"] is True

        r = client.post("/api/playground/test",
                        json={"source": "A", "destination": "E"},
                        headers=OPERATOR).json()
        assert r["delivered"] is True
        assert r["path"] == ["A", "B", "D", "E"]


# ---------------------------------------------------------------------------
# Experiment F — hysteresis
# ---------------------------------------------------------------------------


class TestExperimentF:
    def test_a_detector_can_be_retuned_on_both_ends_of_a_live_link(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)

        r = client.post("/api/playground/detectors", headers=OPERATOR, json={
            "link_id": "A-B", "f_thresh": 2, "n": 4, "r_thresh": 8,
        }).json()
        assert r["hysteresis_ratio"] == 4.0
        endpoints = {d["node_id"] for d in r["detectors"]}
        assert endpoints == {"A", "B"}, "both ends hold a detector for this wire"
        for detector in r["detectors"]:
            assert detector["f_thresh"] == 2
            assert detector["r_thresh"] == 8

    def test_detector_readings_come_from_the_running_nodes(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)
        body = client.get("/api/playground/detectors", headers=READONLY).json()
        assert set(body["detectors"]) == {"A", "B", "C", "D", "E"}
        sample = body["detectors"]["A"]["B"]
        assert sample["state"] in ("HEALTHY", "SUSPECT", "FAILED")
        assert sample["state"] == "HEALTHY"
        assert "consecutive_successes" in sample

    def test_only_states_the_detector_implements_are_claimed(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)
        body = client.get("/api/playground/detectors", headers=READONLY).json()
        assert body["states_implemented"] == ["HEALTHY", "SUSPECT", "FAILED"]
        assert "RECOVERING" not in body["states_implemented"], (
            "this detector has no RECOVERING state; recovery is FAILED → HEALTHY"
        )

    def test_a_real_stop_produces_real_missed_heartbeats(self, client):
        """Stop a node and the neighbouring detector sees the silence."""
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client, heartbeat_interval_s=0.1, f_thresh=1, n=2, r_thresh=2)
        client.post("/api/playground/detectors", headers=OPERATOR, json={
            "link_id": "A-B", "f_thresh": 1, "n": 2, "r_thresh": 2,
        })
        client.post("/api/playground/nodes/B/fail", headers=OPERATOR)

        import time
        deadline = time.time() + 8
        states = []
        while time.time() < deadline:
            detector = client.get(
                "/api/playground/detectors", headers=OPERATOR
            ).json()["detectors"]["A"]["B"]
            states.append(detector["state"])
            if detector["state"] == "FAILED":
                break
            time.sleep(0.15)

        assert "FAILED" in states, f"detector never reached FAILED, saw {set(states)}"

        transitions = client.get(
            "/api/playground/transitions", headers=OPERATOR,
            params={"link_id": "A-B"},
        ).json()["transitions"]
        assert any(t["to_state"] == "SUSPECT" for t in transitions), \
            "it must pass through SUSPECT — there is no HEALTHY → FAILED edge"
        assert any(t["to_state"] == "FAILED" for t in transitions)

    def test_the_comparison_runs_both_detectors_over_one_sequence(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)
        # Quality oscillating around the failure threshold: the case hysteresis
        # exists for. 10 samples, each a window of beats, alternating.
        sequence = (
            [True] * 3 + [False] * 4 + [True] * 4 + [False] * 4 + [True] * 6
        )
        report = client.post("/api/playground/hysteresis/compare", headers=OPERATOR,
                             json={"link_id": "A-B", "sequence": sequence,
                                   "f_thresh": 3, "n": 5, "r_thresh": 5,
                                   "baseline_threshold": 3}).json()

        assert len(report["sequence"]) == len(sequence)
        assert report["hysteresis"]["final_state"] in ("HEALTHY", "SUSPECT", "FAILED")
        assert report["naive"]["final_state"] in ("HEALTHY", "FAILED")
        # Both machines consumed the identical input.
        assert len(report["timeline"]) == len(sequence)
        # A naive two-state detector has no SUSPECT, so it cannot be asked for one.
        assert all(t["to"] != "SUSPECT" for t in report["naive"]["transitions"])
        assert report["route_flaps_saved"] == (
            report["hysteresis"]["route_flap_count"]
            - report["naive"]["route_flap_count"]
        )

    def test_the_comparison_refuses_an_empty_sequence(self, client):
        r = client.post("/api/playground/hysteresis/compare", headers=OPERATOR,
                        json={"link_id": "A-B", "sequence": []})
        assert r.status_code == 422

    def test_a_flapping_link_is_worse_under_the_naive_detector(self, client):
        """
        The measured claim, not an asserted one: over an oscillating sequence the
        single-threshold detector changes the usable path more often.
        """
        sequence = [True] * 3 + [False] * 4 + [True] * 4 + [False] * 4 + [True] * 5
        report = client.post("/api/playground/hysteresis/compare", headers=OPERATOR,
                             json={"link_id": "A-B", "sequence": sequence,
                                   "f_thresh": 3, "n": 5, "r_thresh": 6,
                                   "baseline_threshold": 3}).json()
        assert report["naive"]["route_flap_count"] > report["hysteresis"]["route_flap_count"]


# ---------------------------------------------------------------------------
# Journal and export
# ---------------------------------------------------------------------------


class TestJournal:
    def test_the_journal_records_what_the_backend_returned(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)
        client.post("/api/playground/test", json={"source": "A", "destination": "E"},
                    headers=OPERATOR)
        events = client.get("/api/playground/events", headers=OPERATOR).json()["events"]
        messages = [e["message"] for e in events]
        assert any("Packet delivered" in m for m in messages)
        assert any("Topology saved" in m for m in messages)
        delivery = next(e for e in events if "Packet delivered" in e["message"])
        assert delivery["detail"]["path"] == ["A", "B", "D", "E"]
        assert delivery["ts"] > 0

    def test_events_can_be_cleared_without_stopping_the_network(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)
        client.post("/api/playground/events/clear", headers=OPERATOR)
        assert client.get("/api/playground/events", headers=OPERATOR).json()["count"] == 0
        assert client.get("/api/playground/state", headers=OPERATOR).json()["running"] is True

    def test_export_as_csv_is_the_journal(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)
        client.post("/api/playground/test", json={"source": "A", "destination": "E"},
                    headers=OPERATOR)
        body = client.get("/api/playground/export", headers=OPERATOR,
                          params={"fmt": "csv"}).json()
        assert body["filename"].endswith(".csv")
        assert body["rows"] > 0
        assert "seq,timestamp,iso_time,category" in body["content"]
        assert "Packet delivered" in body["content"]

    def test_export_as_json_carries_the_experiment_not_just_the_events(self, client):
        import json

        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)
        client.post("/api/playground/test", json={"source": "A", "destination": "E"},
                    headers=OPERATOR)
        body = client.get("/api/playground/export", headers=OPERATOR).json()
        document = json.loads(body["content"])
        assert document["topology"]["nodes"][0]["node_id"] == "A"
        assert document["packets"]["delivered"] == 1
        assert document["route"] == ["A", "B", "D", "E"]
        assert document["events"]


# ---------------------------------------------------------------------------
# Problem identification
# ---------------------------------------------------------------------------


class TestProblemIdentification:
    def test_a_problem_is_named_with_its_reason_not_its_appearance(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)
        client.post("/api/playground/links/C-D/fail", headers=OPERATOR)

        state = client.get("/api/playground/state", headers=OPERATOR).json()
        broken = next(l for l in state["links"] if l["link_id"] == "C-D")
        assert broken["issues"][0]["code"] == "link_down"
        assert "routing table" in broken["issues"][0]["message"]

        # The untouched link on the same route is not called a problem for
        # looking like part of the failure.
        healthy = next(l for l in state["links"] if l["link_id"] == "A-B")
        assert healthy["enabled"] is True
        assert not healthy["issues"]

    def test_an_off_route_link_is_a_note_not_a_fault(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)
        state = client.get("/api/playground/state", headers=OPERATOR).json()
        spare = next(l for l in state["links"] if l["link_id"] == "A-C")
        assert spare["issues"] == []
        assert any(n["code"] == "off_route" for n in spare["notes"])

    def test_failing_an_unknown_node_is_404_naming_what_is_known(self, client):
        client.put("/api/playground/topology", json=EXAMPLE, headers=OPERATOR)
        apply(client)
        r = client.post("/api/playground/nodes/Z/fail", headers=OPERATOR)
        assert r.status_code == 404
        assert "Known nodes" in r.json()["detail"]
