"""
scripts/node.py

Basic NetRescue forwarding node process.

Usage:
    python scripts/node.py --node-id A --port 9000
    python scripts/node.py --node-id B --port 9001 --peer 127.0.0.1:9000

Phase 0 goal: nodes exchange identified HELLO/PING messages; confirm DB is reachable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Optional DB import (fails gracefully if not yet installed)
# ---------------------------------------------------------------------------

try:
    from netrescue.core.config import settings
    from netrescue.core.db import create_all_tables, get_session
    from netrescue.core.events import EventType
    from netrescue.core.models import Event, Node

    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("netrescue.node")


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------


def make_message(msg_type: str, node_id: str, seq: int, **extra) -> bytes:
    payload = {"type": msg_type, "node_id": node_id, "seq": seq, "ts": datetime.utcnow().isoformat(), **extra}
    return json.dumps(payload).encode()


def parse_message(data: bytes) -> dict:
    return json.loads(data.decode())


# ---------------------------------------------------------------------------
# Node server
# ---------------------------------------------------------------------------


class NodeServer:
    def __init__(self, node_id: str, host: str, port: int, peers: list[tuple[str, int]]):
        self.node_id = node_id
        self.host = host
        self.port = port
        self.peers = peers
        self.seq = 0
        self._server: asyncio.Server | None = None

    # ----------------------------------------------------------
    # DB registration
    # ----------------------------------------------------------

    def _register_in_db(self) -> None:
        if not DB_AVAILABLE:
            log.warning("DB not available — skipping DB registration.")
            return
        try:
            create_all_tables()
            with get_session() as session:
                existing = session.get(Node, self.node_id)
                if existing is None:
                    session.add(
                        Node(
                            node_id=self.node_id,
                            status="UP",
                            last_seen_at=datetime.utcnow(),
                            software_version="0.1.0",
                        )
                    )
                else:
                    existing.status = "UP"
                    existing.last_seen_at = datetime.utcnow()

                session.add(
                    Event(
                        component="node",
                        event_type=EventType.NODE_STARTED.value,
                        severity="INFO",
                        payload={"node_id": self.node_id, "port": self.port},
                    )
                )
            log.info(f"[{self.node_id}] Registered in PostgreSQL.")
        except Exception as exc:
            log.warning(f"[{self.node_id}] DB registration failed: {exc} — continuing in degraded mode.")

    # ----------------------------------------------------------
    # Handle incoming connections
    # ----------------------------------------------------------

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer_addr = writer.get_extra_info("peername")
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                msg = parse_message(data)
                log.info(f"[{self.node_id}] ← {msg.get('type')} from node={msg.get('node_id')} seq={msg.get('seq')}")

                # Echo PONG for PING
                if msg.get("type") == "PING":
                    self.seq += 1
                    reply = make_message("PONG", self.node_id, self.seq, in_reply_to=msg.get("seq"))
                    writer.write(reply)
                    await writer.drain()
        except Exception as exc:
            log.debug(f"[{self.node_id}] Connection error from {peer_addr}: {exc}")
        finally:
            writer.close()

    # ----------------------------------------------------------
    # Send HELLO/PING to a peer
    # ----------------------------------------------------------

    async def send_ping(self, target_host: str, target_port: int) -> None:
        try:
            reader, writer = await asyncio.open_connection(target_host, target_port)
            self.seq += 1
            msg = make_message("PING", self.node_id, self.seq)
            writer.write(msg)
            await writer.drain()

            response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            parsed = parse_message(response)
            log.info(
                f"[{self.node_id}] ← {parsed.get('type')} from node={parsed.get('node_id')} "
                f"seq={parsed.get('seq')} (reply to {parsed.get('in_reply_to')})"
            )
            writer.close()
            await writer.wait_closed()
        except Exception as exc:
            log.warning(f"[{self.node_id}] Ping to {target_host}:{target_port} failed: {exc}")

    # ----------------------------------------------------------
    # Main lifecycle
    # ----------------------------------------------------------

    async def run(self) -> None:
        self._register_in_db()

        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        log.info(f"[{self.node_id}] Listening on {self.host}:{self.port}")

        # Wait a moment, then ping each peer
        await asyncio.sleep(1.0)
        for peer_host, peer_port in self.peers:
            await self.send_ping(peer_host, peer_port)

        async with self._server:
            await self._server.serve_forever()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NetRescue node process (Phase 0)")
    parser.add_argument("--node-id", required=True, help="Unique node identifier")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, required=True, help="TCP listen port")
    parser.add_argument(
        "--peer",
        action="append",
        default=[],
        metavar="HOST:PORT",
        help="Peer address to ping on startup (repeatable)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    peers: list[tuple[str, int]] = []
    for p in args.peer:
        host, port = p.rsplit(":", 1)
        peers.append((host, int(port)))

    node = NodeServer(node_id=args.node_id, host=args.host, port=args.port, peers=peers)
    try:
        asyncio.run(node.run())
    except KeyboardInterrupt:
        log.info(f"[{args.node_id}] Shutting down.")


if __name__ == "__main__":
    main()
