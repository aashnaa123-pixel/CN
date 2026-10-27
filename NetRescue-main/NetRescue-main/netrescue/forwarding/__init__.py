"""
netrescue.forwarding — real overlay forwarding over TCP sockets (Feature A).

  node.py : ForwardingNode, wire framing, forwarding table with version guards

A node is an independent OS process with a real TCP listener, an explicit
forwarding table, heartbeat-driven failure detection, and loop/stale-route
protection. The forwarding hot path never queries the database; losing
PostgreSQL degrades telemetry, not forwarding (PRD section 2.2 rule 1).
"""

from netrescue.forwarding.node import (
    ForwardEntry,
    ForwardingNode,
    ForwardingTable,
    Message,
    ProtocolError,
    decode,
    link_name,
    sign_payload,
    verify_payload,
)

__all__ = [
    "ForwardingNode",
    "ForwardingTable",
    "ForwardEntry",
    "Message",
    "ProtocolError",
    "decode",
    "link_name",
    "sign_payload",
    "verify_payload",
]