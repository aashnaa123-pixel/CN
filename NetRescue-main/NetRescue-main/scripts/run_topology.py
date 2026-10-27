"""
scripts/run_topology.py — run a real overlay topology as local node processes.

Replaces the Phase 0 stub. Starts N forwarding nodes, wires them into a line or
ring, and optionally sends traffic across the overlay so failure and recovery
can be observed end to end.

Usage::

    # 3-node line A-B-C, forwarding enabled
    python scripts/run_topology.py --nodes A,B,C --layout line --demo

    # ring, with a shared HMAC key
    python scripts/run_topology.py --nodes A,B,C,D --layout ring --key secret

    # single node (useful for checking startup)
    python scripts/run_topology.py --nodes A --layout line

Ctrl-C stops everything. All events go to stdout as JSON lines so a harness or
the API can consume them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netrescue.forwarding import ForwardingNode, link_name
from netrescue.security.hmac_auth import load_key

log = logging.getLogger("netrescue.topology")


def emit(event_type: str, payload: dict) -> None:
    """Print one event as a JSON line — machine-readable, no log noise."""
    print(json.dumps({"event": event_type, "ts": time.time(), **payload}), flush=True)


def build_edges(nodes: list[str], layout: str) -> list[tuple[str, str]]:
    if layout == "line":
        return [(nodes[i], nodes[i + 1]) for i in range(len(nodes) - 1)]
    if layout == "ring":
        return [(nodes[i], nodes[(i + 1) % len(nodes)]) for i in range(len(nodes))]
    if layout == "full":
        return [
            (nodes[i], nodes[j])
            for i in range(len(nodes))
            for j in range(i + 1, len(nodes))
        ]
    raise ValueError(f"unknown layout {layout!r}")


async def run(args: argparse.Namespace) -> int:
    nodes = [n.strip() for n in args.nodes.split(",") if n.strip()]
    if len(nodes) < 1:
        print("at least one node is required", file=sys.stderr)
        return 2

    key = load_key(args.key) if args.key else b""
    base_port = args.base_port
    port_of = {name: base_port + i for i, name in enumerate(nodes)}

    running: dict[str, ForwardingNode] = {}
    for name in nodes:
        running[name] = ForwardingNode(
            node_id=name,
            host=args.host,
            port=port_of[name],
            shared_key=key,
            heartbeat_interval_s=args.heartbeat_interval,
            on_event=emit,
        )

    edges = build_edges(nodes, args.layout)

    for a, b in edges:
        running[a].add_neighbour(b, args.host, port_of[b])
        running[b].add_neighbour(a, args.host, port_of[a])

    # Every node needs the whole topology to compute multi-hop paths and
    # alternate routes; local links alone cannot do that.
    for node in running.values():
        node.install_topology(nodes, edges)

    for node in running.values():
        await node.start()
        node.recompute_routes()

    beats = [asyncio.create_task(n.heartbeat_loop()) for n in running.values()]

    for name in nodes:
        emit("topology.ready", {
            "node_id": name,
            "port": port_of[name],
            "routes": running[name].recompute_routes(),
        })

    if args.demo and len(nodes) >= 2:
        await demo(running, nodes, port_of, args)

    if args.hold:
        emit("topology.holding", {"note": "Ctrl-C to stop"})
        try:
            await asyncio.gather(*[n.serve_forever() for n in running.values()])
        except asyncio.CancelledError:
            pass
    else:
        await asyncio.sleep(args.duration)

    for t in beats:
        t.cancel()
    for node in running.values():
        await node.stop()

    emit("topology.stopped", {
        "nodes": [n.status() for n in running.values()],
    })
    return 0


async def demo(running: dict, nodes: list[str], port_of: dict, args) -> None:
    """
    Send traffic end to end, then fail a middle node and observe recovery.

    This is the observable proof that forwarding works: a message reaches the
    far node before the fault, and still arrives after the fault via the
    alternate route.
    """
    first, last = nodes[0], nodes[-1]
    body = f"hello from {first} to {last}".encode()

    emit("demo.start", {"from": first, "to": last})
    ok = await running[first].send_data(
        args.host, port_of[nodes[1]] if len(nodes) > 1 else port_of[first],
        destination=last, body=body,
    )
    emit("demo.baseline", {"delivered": ok})

    if len(nodes) >= 3:
        victim = nodes[1]
        emit("demo.fault", {
            "node_id": victim,
            "note": "marking the A-B link failed on every node",
        })
        for node in running.values():
            node.router.mark_failed(link_name(node.node_id, victim))
            node.recompute_routes()

        await asyncio.sleep(args.fault_pause)

        routes_after = running[first].recompute_routes()
        ok2 = await running[first].send_data(
            args.host, port_of[nodes[1]], destination=last, body=body,
        )
        emit("demo.after_fault", {
            "delivered": ok2,
            "recovered_via": routes_after.get(last),
            "routes": routes_after,
            "counters": running[first].status()["counters"],
        })


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a local NetRescue overlay topology")
    parser.add_argument("--nodes", default="A,B,C", help="comma-separated node ids")
    parser.add_argument("--layout", default="line", choices=["line", "ring", "full"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--base-port", type=int, default=9400)
    parser.add_argument("--heartbeat", type=float, default=1.0, dest="heartbeat_interval")
    parser.add_argument("--key", default=None, help="shared HMAC key (hex or base64)")
    parser.add_argument("--duration", type=float, default=3.0, help="seconds to run without --hold")
    parser.add_argument("--hold", action="store_true", help="run until Ctrl-C")
    parser.add_argument("--demo", action="store_true", help="send traffic and inject a fault")
    parser.add_argument("--fault-pause", type=float, default=1.5, dest="fault_pause")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
