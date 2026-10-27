"""
netrescue/security/audit.py

Administrative audit trail (PRD §9, §7 AuditEvent).

Records who did what to which resource and whether it was allowed. Security
events are logged through here so rejections are visible rather than silent.

Hard rule, enforced in code: **no secret ever reaches this log.** Payloads pass
through `scrub()`, which redacts anything whose key looks like a credential and
truncates oversized values. `record()` is the only write path.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: Substrings that mark a key as credential-bearing.
SECRET_KEY_HINTS = (
    "key", "secret", "password", "passwd", "token", "mac", "sig",
    "credential", "private", "cert", "passphrase", "hmac",
)

REDACTED = "[redacted]"

#: Cap on a single audit detail value, so a dump cannot bloat the table.
MAX_DETAIL_CHARS = 512

#: A 64-hex-digit string is almost certainly a hash or a MAC — redact it too.
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


def scrub(value: Any, _depth: int = 0) -> Any:
    """
    Recursively redact credential-shaped values.

    Applied to every audit payload. Nested structures are walked so a secret
    cannot hide one level down inside `detail`.
    """
    if _depth > 6:
        return "[truncated:depth]"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if any(hint in str(k).lower() for hint in SECRET_KEY_HINTS):
                out[k] = REDACTED
            else:
                out[k] = scrub(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(v, _depth + 1) for v in value[:100]]
    if isinstance(value, str):
        if _HEX64.match(value.strip()):
            return REDACTED
        if len(value) > MAX_DETAIL_CHARS:
            return value[:MAX_DETAIL_CHARS] + f"...[+{len(value) - MAX_DETAIL_CHARS} chars]"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_DETAIL_CHARS]


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


@dataclass
class AuditRecord:
    timestamp: datetime
    principal: str
    action: str
    resource: str
    result: str  # ALLOW | DENY
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "principal": self.principal,
            "action": self.action,
            "resource": self.resource,
            "result": self.result,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Auditor
# ---------------------------------------------------------------------------


class Auditor:
    """
    In-memory audit log with optional PostgreSQL persistence.

    The in-memory list is always populated so tests and the API can assert on
    rejections without needing a database. Persistence is best-effort: a failed
    write degrades with a warning rather than taking down the operation being
    audited — except for DENY records, which must not be silently lost, so those
    are re-raised as warnings in the log.
    """

    def __init__(self, db_session=None, persist: bool = True,
                 session_factory=None, flush_interval_s: float = 1.0,
                 max_pending: int = 5000):
        """
        `db_session` is a live session, used for tests and for callers that
        already hold one. `session_factory` is a zero-argument callable returning
        a *context manager* yielding a fresh session.

        Either way, records are queued and flushed by a background thread rather
        than written inline, so no request ever blocks on an audit write.
        """
        self.records: list[AuditRecord] = []
        self._db_session = db_session
        self._session_factory = session_factory
        self._persist = persist
        self.flush_interval_s = flush_interval_s
        self.max_pending = max_pending
        self._pending: deque[AuditRecord] = deque()
        self._queue_lock = threading.Lock()
        self._flusher: threading.Thread | None = None
        self._stop = threading.Event()
        #: Records that could not be written, so a gap is discoverable.
        self.persist_failures = 0
        #: Records dropped because the queue hit its cap.
        self.dropped = 0
        self._records_written = 0

    # ------------------------------------------------------------------

    def record(
        self,
        principal: str,
        action: str,
        resource: str,
        allowed: bool,
        detail: Any = None,
    ) -> AuditRecord:
        """Append one audit entry. The only write path."""
        rec = AuditRecord(
            timestamp=datetime.utcnow(),
            principal=str(principal)[:128],
            action=str(action)[:64],
            resource=str(resource)[:256],
            result="ALLOW" if allowed else "DENY",
            detail=None if detail is None else _render(scrub(detail)),
        )
        self.records.append(rec)

        level = logging.INFO if allowed else logging.WARNING
        log.log(level, "[audit] %s %s %s -> %s", rec.principal, rec.action, rec.resource, rec.result)

        self._persist_record(rec)
        return rec

    def allow(self, principal: str, action: str, resource: str, detail: Any = None) -> AuditRecord:
        return self.record(principal, action, resource, True, detail)

    def deny(self, principal: str, action: str, resource: str, reason: Any = None) -> AuditRecord:
        """Record a denial. `reason` is scrubbed like any other payload."""
        return self.record(principal, action, resource, False, reason)

    # ------------------------------------------------------------------

    def _persist_record(self, rec: AuditRecord) -> None:
        """
        Queue the record for durable storage.

        Deliberately does not write inline. A synchronous write here would open a
        second connection from the same pool on every audited request — including
        requests that are themselves queries — which starves the pool and turns
        each request into seconds of latency. Records are therefore queued and
        flushed by a background thread, so auditing never costs the caller
        anything and the trail still survives a restart.
        """
        if not self._persist:
            return
        with self._queue_lock:
            if len(self._pending) >= self.max_pending:
                # Never grow without bound. Dropping the oldest is a deliberate,
                # visible choice: a trail that silently stops is worse than one
                # with a declared cap.
                self._pending.popleft()
                self.dropped += 1
            self._pending.append(rec)
        self._ensure_flusher()

    def _flush(self) -> None:
        """Write every queued record in one session. Runs on the flusher thread."""
        while True:
            with self._queue_lock:
                batch = list(self._pending)
                self._pending.clear()
            if not batch:
                return
            if self._session_factory is not None:
                try:
                    with self._session_factory() as session:
                        for rec in batch:
                            self._write(session, rec)
                    continue
                except Exception as exc:
                    self.persist_failures += len(batch)
                    log.warning("[audit] could not persist %d record(s): %s",
                                len(batch), exc)
                    continue
            if self._db_session is not None:
                try:
                    for rec in batch:
                        self._write(self._db_session, rec)
                    continue
                except Exception as exc:
                    self.persist_failures += len(batch)
                    log.warning("[audit] could not persist %d record(s): %s",
                                len(batch), exc)
                    continue
            return

    def _ensure_flusher(self) -> None:
        if self._session_factory is None and self._db_session is None:
            return
        with self._queue_lock:
            if self._flusher is not None and self._flusher.is_alive():
                return
            self._flusher = threading.Thread(
                target=self._flusher_loop, name="netrescue-audit", daemon=True
            )
            self._flusher.start()

    def _flusher_loop(self) -> None:
        while not self._stop.is_set():
            self._flush()
            self._stop.wait(self.flush_interval_s)

    def flush_now(self) -> int:
        """Write everything queued. Returns the total records written so far."""
        self._flush()
        return self._records_written

    def _write(self, session, rec: AuditRecord) -> None:
        from netrescue.core.models import AuditEvent

        session.add(AuditEvent(
            timestamp=rec.timestamp,
            principal=rec.principal,
            action=rec.action,
            resource=rec.resource,
            result=rec.result,
            detail=rec.detail,
        ))
        session.commit()
        self._records_written += 1

    # ------------------------------------------------------------------

    def find(self, *, result: str | None = None, action: str | None = None,
             principal: str | None = None) -> list[AuditRecord]:
        """Query the in-memory log, newest last."""
        out = self.records
        if result is not None:
            out = [r for r in out if r.result == result]
        if action is not None:
            out = [r for r in out if r.action == action]
        if principal is not None:
            out = [r for r in out if r.principal == principal]
        return list(out)

    def to_rows(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.records]

    def clear(self) -> None:
        self.records.clear()


def _render(detail: Any) -> str:
    if isinstance(detail, str):
        return detail[:MAX_DETAIL_CHARS]
    import json
    try:
        return json.dumps(detail, sort_keys=True, default=str)[:MAX_DETAIL_CHARS]
    except Exception:
        return str(detail)[:MAX_DETAIL_CHARS]


# ---------------------------------------------------------------------------
# Module-level default
# ---------------------------------------------------------------------------

_default: Auditor | None = None


def default_auditor() -> Auditor:
    """
    Process-wide auditor used by the API layer.

    Persisting is on: an audit trail that only exists in memory and vanishes on
    restart cannot be relied on to answer "who did this". Each write opens its own
    short-lived session, so concurrent requests do not share one.

    If PostgreSQL is unreachable the record is still kept in memory and the
    failure is counted on `persist_failures`, which /api/audit reports.
    """
    global _default
    if _default is None:
        from netrescue.core.db import get_session

        _default = Auditor(persist=True, session_factory=get_session)
    return _default


def audit_persistence_status() -> dict:
    """How the trail is doing. Surfaced by /api/audit so a gap is visible."""
    if _default is None:
        return {"persist_enabled": False, "persist_failures": 0,
                "dropped": 0, "queued": 0, "written": 0,
                "in_memory_records": 0}
    return {
        "persist_enabled": _default._persist,
        "persist_failures": _default.persist_failures,
        "dropped": _default.dropped,
        "queued": len(_default._pending),
        "written": _default._records_written,
        "in_memory_records": len(_default.records),
    }