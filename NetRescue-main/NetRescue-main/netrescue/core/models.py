"""
netrescue/core/models.py

SQLAlchemy ORM models for all 14 NetRescue database entities.
Matches data model in PRD §7.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


class Node(Base):
    """Software forwarding process / logical node in the overlay topology."""

    __tablename__ = "nodes"

    node_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    status: Mapped[str] = mapped_column(
        Enum("UP", "DOWN", "DEGRADED", name="node_status_enum"), default="UP"
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    software_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    # relationships
    links_as_a: Mapped[list["Link"]] = relationship(
        "Link", foreign_keys="Link.node_a_id", back_populates="node_a"
    )
    links_as_b: Mapped[list["Link"]] = relationship(
        "Link", foreign_keys="Link.node_b_id", back_populates="node_b"
    )

    def __repr__(self) -> str:
        return f"<Node {self.node_id!r} status={self.status!r}>"


# ---------------------------------------------------------------------------
# Link
# ---------------------------------------------------------------------------


class Link(Base):
    """Logical link between two nodes in the overlay."""

    __tablename__ = "links"

    link_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    node_a_id: Mapped[str] = mapped_column(ForeignKey("nodes.node_id"))
    node_b_id: Mapped[str] = mapped_column(ForeignKey("nodes.node_id"))
    status: Mapped[str] = mapped_column(
        Enum("UP", "DOWN", name="link_status_enum"), default="UP"
    )
    capacity_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    loss_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    node_a: Mapped["Node"] = relationship("Node", foreign_keys=[node_a_id], back_populates="links_as_a")
    node_b: Mapped["Node"] = relationship("Node", foreign_keys=[node_b_id], back_populates="links_as_b")

    def __repr__(self) -> str:
        return f"<Link {self.link_id!r} {self.node_a_id}↔{self.node_b_id} status={self.status!r}>"


# ---------------------------------------------------------------------------
# Experiment / Run
# ---------------------------------------------------------------------------


class Experiment(Base):
    """Top-level container for a reproducible test run."""

    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    feature: Mapped[str] = mapped_column(String(8))  # "A", "C", "D"
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    code_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("PENDING", "RUNNING", "DONE", "FAILED", name="experiment_status_enum"),
        default="PENDING",
    )
    environment: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    metric_samples: Mapped[list["MetricSample"]] = relationship("MetricSample", back_populates="experiment")
    events: Mapped[list["Event"]] = relationship("Event", back_populates="experiment")
    state_transitions: Mapped[list["StateTransition"]] = relationship("StateTransition", back_populates="experiment")
    transfers: Mapped[list["Transfer"]] = relationship("Transfer", back_populates="experiment")

    def __repr__(self) -> str:
        return f"<Experiment {self.id!r} feature={self.feature!r} status={self.status!r}>"


# ---------------------------------------------------------------------------
# EmulationConfig
# ---------------------------------------------------------------------------


class EmulationConfig(Base):
    """Per-path tc/netem configuration for a run (PRD §6.3)."""

    __tablename__ = "emulation_configs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"))
    link_id: Mapped[str] = mapped_column(ForeignKey("links.link_id"))
    delay_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    jitter_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    loss_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    duplication_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    corruption_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    reorder_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    rate_limit_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    queue_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)

    def __repr__(self) -> str:
        return f"<EmulationConfig run={self.run_id!r} link={self.link_id!r}>"


# ---------------------------------------------------------------------------
# HysteresisConfig
# ---------------------------------------------------------------------------


class HysteresisConfig(Base):
    """Hysteresis detector parameters for a link in a run (PRD §4.3)."""

    __tablename__ = "hysteresis_configs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"))
    link_id: Mapped[str] = mapped_column(ForeignKey("links.link_id"))
    f_thresh: Mapped[int] = mapped_column(Integer, default=3)
    n: Mapped[int] = mapped_column(Integer, default=5)
    r_thresh: Mapped[int] = mapped_column(Integer, default=5)
    hysteresis_ratio: Mapped[float] = mapped_column(Float, default=1.0)
    heartbeat_interval_s: Mapped[float] = mapped_column(Float, default=1.0)
    use_ewma: Mapped[bool] = mapped_column(Boolean, default=False)

    def __repr__(self) -> str:
        return f"<HysteresisConfig run={self.run_id!r} ratio={self.hysteresis_ratio}>"


# ---------------------------------------------------------------------------
# StateTransition
# ---------------------------------------------------------------------------


class StateTransition(Base):
    """Every hysteresis state machine transition (PRD §4.3)."""

    __tablename__ = "state_transitions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"))
    link_id: Mapped[str] = mapped_column(ForeignKey("links.link_id"))
    from_state: Mapped[str] = mapped_column(String(16))
    to_state: Mapped[str] = mapped_column(String(16))
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now)
    route_flap: Mapped[bool] = mapped_column(Boolean, default=False)

    experiment: Mapped["Experiment"] = relationship("Experiment", back_populates="state_transitions")

    def __repr__(self) -> str:
        return f"<StateTransition {self.from_state}→{self.to_state} link={self.link_id!r}>"


# ---------------------------------------------------------------------------
# RouteVersion
# ---------------------------------------------------------------------------


class RouteVersion(Base):
    """Versioned routing decisions (PRD §4.3 — loop protection)."""

    __tablename__ = "route_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    version: Mapped[int] = mapped_column(Integer)
    source_node_id: Mapped[str] = mapped_column(ForeignKey("nodes.node_id"))
    destination_node_id: Mapped[str] = mapped_column(ForeignKey("nodes.node_id"))
    next_hops: Mapped[list[str]] = mapped_column(JSON, default=list)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)

    def __repr__(self) -> str:
        return f"<RouteVersion v{self.version} {self.source_node_id}→{self.destination_node_id}>"


# ---------------------------------------------------------------------------
# MetricSample
# ---------------------------------------------------------------------------


class MetricSample(Base):
    """Time-series metric samples; batched at ≥ 1 Hz (PRD §2.2 rule)."""

    __tablename__ = "metric_samples"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now)
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    labels: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)

    experiment: Mapped["Experiment"] = relationship("Experiment", back_populates="metric_samples")

    def __repr__(self) -> str:
        return f"<MetricSample {self.name}={self.value!r} run={self.run_id!r}>"


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------


class Event(Base):
    """Diagnostic event log (PRD §4.3 observability requirements)."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("experiments.id"), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now)
    severity: Mapped[str] = mapped_column(
        Enum("DEBUG", "INFO", "WARNING", "ERROR", name="event_severity_enum"),
        default="INFO",
    )
    component: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    experiment: Mapped["Experiment | None"] = relationship("Experiment", back_populates="events")

    def __repr__(self) -> str:
        return f"<Event {self.event_type!r} [{self.severity}]>"


# ---------------------------------------------------------------------------
# AuditEvent
# ---------------------------------------------------------------------------


class AuditEvent(Base):
    """Administrative accountability log (PRD §9 — no secrets stored here)."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now)
    principal: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    resource: Mapped[str] = mapped_column(String(256))
    result: Mapped[str] = mapped_column(
        Enum("ALLOW", "DENY", name="audit_result_enum")
    )
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<AuditEvent {self.principal!r} {self.action!r} → {self.result!r}>"


# ---------------------------------------------------------------------------
# Transfer / Chunk
# ---------------------------------------------------------------------------


class Transfer(Base):
    """Multipath file transfer session (Feature C, PRD §5.3)."""

    __tablename__ = "transfers"

    transfer_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"))
    source_node_id: Mapped[str] = mapped_column(ForeignKey("nodes.node_id"))
    dest_node_id: Mapped[str] = mapped_column(ForeignKey("nodes.node_id"))
    filename: Mapped[str] = mapped_column(String(256))
    file_size_bytes: Mapped[int] = mapped_column(Integer)
    total_chunks: Mapped[int] = mapped_column(Integer)
    scheduler_variant: Mapped[str] = mapped_column(String(32), default="DB-DSP")
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receiver_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    integrity_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("PENDING", "IN_PROGRESS", "COMPLETE", "FAILED", name="transfer_status_enum"),
        default="PENDING",
    )

    experiment: Mapped["Experiment"] = relationship("Experiment", back_populates="transfers")
    chunks: Mapped[list["Chunk"]] = relationship("Chunk", back_populates="transfer")

    def __repr__(self) -> str:
        return f"<Transfer {self.transfer_id!r} {self.filename!r} status={self.status!r}>"


class Chunk(Base):
    """Single file chunk record (PRD §5.3)."""

    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    transfer_id: Mapped[str] = mapped_column(ForeignKey("transfers.transfer_id"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    total_chunks: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    path_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        Enum("PENDING", "IN_FLIGHT", "DELIVERED", "FAILED", name="chunk_status_enum"),
        default="PENDING",
    )

    transfer: Mapped["Transfer"] = relationship("Transfer", back_populates="chunks")

    __table_args__ = (UniqueConstraint("transfer_id", "chunk_index"),)

    def __repr__(self) -> str:
        return f"<Chunk {self.chunk_index}/{self.total_chunks} status={self.status!r}>"


# ---------------------------------------------------------------------------
# PathEstimate
# ---------------------------------------------------------------------------


class PathEstimate(Base):
    """EWMA scheduler state snapshots per path per run (PRD §5.3)."""

    __tablename__ = "path_estimates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"))
    path_id: Mapped[str] = mapped_column(String(64))
    fd_estimate_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp_estimate_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    cwnd_estimate_bytes: Mapped[float | None] = mapped_column(Float, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now)

    def __repr__(self) -> str:
        return f"<PathEstimate path={self.path_id!r} fd={self.fd_estimate_ms}ms>"


# ---------------------------------------------------------------------------
# ComparisonRun
# ---------------------------------------------------------------------------


class ComparisonRun(Base):
    """Scheduler / RTX policy comparison results per variant (PRD §5.4)."""

    __tablename__ = "comparison_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"))
    variant: Mapped[str] = mapped_column(String(32))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now)

    def __repr__(self) -> str:
        return f"<ComparisonRun variant={self.variant!r} run={self.run_id!r}>"


# ---------------------------------------------------------------------------
# TcConfig
# ---------------------------------------------------------------------------


class TcConfig(Base):
    """Applied tc/netem configuration audit record (PRD §6.3)."""

    __tablename__ = "tc_configs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"))
    link_id: Mapped[str] = mapped_column(ForeignKey("links.link_id"))
    qdisc_params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    config_hash: Mapped[str] = mapped_column(String(64))
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<TcConfig run={self.run_id!r} link={self.link_id!r} verified={self.verified}>"
