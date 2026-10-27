"""
netrescue/core/events.py

Shared event type definitions used across all NetRescue modules.
Events are emitted to the database (Event table) and broadcast via WebSocket.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# Component identifiers
# ---------------------------------------------------------------------------


class Component(str, Enum):
    HEARTBEAT = "heartbeat"
    STATE_MACHINE = "state_machine"
    ROUTER = "router"
    TRANSFER = "transfer"
    EMULATION = "emulation"
    BENCHMARK = "benchmark"
    SECURITY = "security"
    API = "api"
    NODE = "node"


# ---------------------------------------------------------------------------
# Event type strings (used in Event.event_type column)
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    # Feature A — Recovery
    HEARTBEAT_SENT = "heartbeat.sent"
    HEARTBEAT_RECEIVED = "heartbeat.received"
    HEARTBEAT_MISSED = "heartbeat.missed"
    STATE_TRANSITION = "state_machine.transition"
    ROUTE_UPDATED = "router.route_updated"
    ROUTE_FLAP = "router.route_flap"
    DESTINATION_UNREACHABLE = "router.unreachable"

    # Feature C — Transfer
    TRANSFER_STARTED = "transfer.started"
    TRANSFER_CHUNK_SENT = "transfer.chunk_sent"
    TRANSFER_CHUNK_DELIVERED = "transfer.chunk_delivered"
    TRANSFER_CHUNK_FAILED = "transfer.chunk_failed"
    TRANSFER_RETRANSMIT = "transfer.retransmit"
    TRANSFER_COMPLETE = "transfer.complete"
    TRANSFER_FAILED = "transfer.failed"

    # Feature D — Emulation / Benchmark
    EMULATION_APPLIED = "emulation.applied"
    EMULATION_VERIFIED = "emulation.verified"
    BENCHMARK_STARTED = "benchmark.started"
    BENCHMARK_COMPLETE = "benchmark.complete"

    # Security
    AUTH_SUCCESS = "security.auth_success"
    AUTH_FAILURE = "security.auth_failure"
    REPLAY_REJECTED = "security.replay_rejected"
    CERT_REJECTED = "security.cert_rejected"
    UNAUTHORISED_ACTION = "security.unauthorised"

    # System
    NODE_STARTED = "node.started"
    NODE_STOPPED = "node.stopped"
    DB_DEGRADED = "system.db_degraded"
    CONTROLLER_DEGRADED = "system.controller_degraded"


# ---------------------------------------------------------------------------
# In-process event dataclasses (not persisted — used for in-memory dispatch)
# ---------------------------------------------------------------------------


@dataclass
class NetRescueEvent:
    """
    Base class for all in-process events.

    `event_type` and `component` are defaulted rather than required because
    every concrete subclass assigns its own pair in `__post_init__`. Declaring
    them as required dataclass fields would turn them into positional
    constructor arguments that callers must pass, only for the value to be
    immediately overwritten.
    """

    event_type: EventType | None = None
    component: Component | None = None
    severity: Severity = Severity.INFO
    timestamp: datetime = field(default_factory=datetime.utcnow)
    run_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class StateTransitionEvent(NetRescueEvent):
    """Emitted by the hysteresis state machine on every state change."""

    link_id: str = ""
    from_state: str = ""
    to_state: str = ""
    route_flap: bool = False

    def __post_init__(self) -> None:
        self.event_type = EventType.STATE_TRANSITION
        self.component = Component.STATE_MACHINE
        self.payload = {
            "link_id": self.link_id,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "route_flap": self.route_flap,
        }


@dataclass
class HeartbeatEvent(NetRescueEvent):
    """Emitted when a heartbeat is sent, received, or missed."""

    link_id: str = ""
    sequence: int = 0
    missed_count: int = 0

    def __post_init__(self) -> None:
        self.component = Component.HEARTBEAT
        self.payload = {
            "link_id": self.link_id,
            "sequence": self.sequence,
            "missed_count": self.missed_count,
        }


@dataclass
class TransferEvent(NetRescueEvent):
    """Emitted during multipath file transfer."""

    transfer_id: str = ""
    chunk_index: int | None = None
    path_id: str | None = None

    def __post_init__(self) -> None:
        self.component = Component.TRANSFER
        self.payload = {
            "transfer_id": self.transfer_id,
            "chunk_index": self.chunk_index,
            "path_id": self.path_id,
        }


@dataclass
class SecurityEvent(NetRescueEvent):
    """Emitted on authentication / authorisation events (no secrets in payload)."""

    principal: str = ""
    action: str = ""
    resource: str = ""
    result: str = ""  # "ALLOW" or "DENY"

    def __post_init__(self) -> None:
        self.component = Component.SECURITY
        self.payload = {
            "principal": self.principal,
            "action": self.action,
            "resource": self.resource,
            "result": self.result,
        }
