"""apps/api/routers/nodes.py — Node and Link CRUD endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from apps.api.deps import DbSession, RequireOperator, RequireRead
from netrescue.core.models import Link, Node
from netrescue.security.audit import default_auditor

router = APIRouter(prefix="/api", tags=["topology"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class NodeCreate(BaseModel):
    node_id: str
    status: str = "UP"
    software_version: Optional[str] = None


class NodeOut(BaseModel):
    node_id: str
    status: str
    last_seen_at: Optional[datetime]
    software_version: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class LinkCreate(BaseModel):
    link_id: str
    node_a_id: str
    node_b_id: str
    status: str = "UP"
    capacity_bps: Optional[float] = None
    latency_ms: Optional[float] = None
    loss_rate: Optional[float] = None


class LinkOut(BaseModel):
    link_id: str
    node_a_id: str
    node_b_id: str
    status: str
    capacity_bps: Optional[float]
    latency_ms: Optional[float]
    loss_rate: Optional[float]
    created_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Node endpoints
# ---------------------------------------------------------------------------


@router.get("/nodes", response_model=list[NodeOut])
def list_nodes(db: DbSession, _: RequireRead):
    """List all nodes in the topology."""
    return db.query(Node).all()


@router.post("/nodes", response_model=NodeOut, status_code=status.HTTP_201_CREATED)
def create_node(body: NodeCreate, db: DbSession, principal: RequireOperator):
    """Register a new forwarding node."""
    if db.query(Node).filter(Node.node_id == body.node_id).first():
        raise HTTPException(status_code=409, detail=f"Node '{body.node_id}' already exists.")
    node = Node(**body.model_dump())
    db.add(node)
    db.commit()
    db.refresh(node)
    default_auditor().record(principal.name, "node.create", body.node_id, True)
    return node


@router.get("/nodes/{node_id}", response_model=NodeOut)
def get_node(node_id: str, db: DbSession, _: RequireRead):
    """Get a single node by ID."""
    node = db.query(Node).filter(Node.node_id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found.")
    return node


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_node(node_id: str, db: DbSession, principal: RequireOperator):
    """Remove a node from the topology."""
    node = db.query(Node).filter(Node.node_id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found.")
    db.delete(node)
    db.commit()
    default_auditor().record(principal.name, "node.delete", node_id, True)


# ---------------------------------------------------------------------------
# Link endpoints
# ---------------------------------------------------------------------------


@router.get("/links", response_model=list[LinkOut])
def list_links(db: DbSession, _: RequireRead):
    """List all configured links."""
    return db.query(Link).all()


@router.post("/links", response_model=LinkOut, status_code=status.HTTP_201_CREATED)
def create_link(body: LinkCreate, db: DbSession, principal: RequireOperator):
    """Add a link between two nodes."""
    if db.query(Link).filter(Link.link_id == body.link_id).first():
        raise HTTPException(status_code=409, detail=f"Link '{body.link_id}' already exists.")
    link = Link(**body.model_dump())
    db.add(link)
    db.commit()
    db.refresh(link)
    default_auditor().record(principal.name, "link.create", body.link_id, True)
    return link


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_link(link_id: str, db: DbSession, principal: RequireOperator):
    """Remove a link from the topology."""
    link = db.query(Link).filter(Link.link_id == link_id).first()
    if not link:
        raise HTTPException(status_code=404, detail=f"Link '{link_id}' not found.")
    db.delete(link)
    db.commit()
    default_auditor().record(principal.name, "link.delete", link_id, True)
