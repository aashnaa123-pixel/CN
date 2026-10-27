"""
netrescue/core/db.py

Database session factory and helpers.
Uses SQLAlchemy 2.x synchronous engine backed by psycopg3.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from netrescue.core.config import settings
from netrescue.core.models import Base

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Degraded-mode circuit breaker (PRD §2.2 rule 3)
# ---------------------------------------------------------------------------
#
# "If the controller or PostgreSQL becomes unavailable, nodes continue
#  forwarding using last valid in-memory state and enter a visible degraded
#  mode."
#
# Without this, every metric flush blocks for the full driver connect timeout
# against an unreachable database. A benchmark that writes one batch per run
# would spend all of its time waiting on a socket. After a failure we stop
# trying for COOLDOWN_S and report degraded state instead.

COOLDOWN_S = 30.0
_lock = threading.Lock()
_unavailable_until: float = 0.0
_last_error: str = ""


def mark_unavailable(error: str) -> None:
    """Record a database failure and open the breaker."""
    global _unavailable_until, _last_error
    with _lock:
        _unavailable_until = time.monotonic() + COOLDOWN_S
        _last_error = error
    log.warning(
        "[db] entering degraded mode for %.0fs: %s", COOLDOWN_S, error
    )


def mark_available() -> None:
    """Clear the breaker after a successful connection."""
    global _unavailable_until, _last_error
    with _lock:
        _unavailable_until = 0.0
        _last_error = ""


def is_degraded() -> bool:
    """True while the breaker is open (database believed unreachable)."""
    with _lock:
        return time.monotonic() < _unavailable_until


def degraded_reason() -> str:
    with _lock:
        return _last_error


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
#
# Connection strings arrive from the environment, and a URL that is perfectly
# valid for one driver is rejected outright by another. Two shapes are common
# enough to be worth handling rather than crashing on:
#
#   * a bare `postgresql://` with no driver, which SQLAlchemy may resolve to a
#     driver that is not installed (psycopg2). This project ships psycopg3, so
#     the driver is pinned explicitly.
#   * a hosted-Postgres pooler URL carrying `?pgbouncer=true`. That switch is
#     meaningful to the provider's own client, but psycopg3 rejects unknown
#     connection options outright and the whole connection fails with
#     `invalid connection option "pgbouncer"`. psycopg3 already handles pooled
#     connections; the parameter is simply noise here and is dropped.


#: Query parameters psycopg3 does not understand and that must not be forwarded.
_STRIPPED_PARAMS = ("pgbouncer", "pooler", "sslmode_prefer")


def _is_postgres(url: str) -> bool:
    return url.startswith("postgres")


def normalise_database_url(url: str) -> str:
    """
    Make a user-supplied DATABASE_URL safe to hand to SQLAlchemy.

    Never raises: a URL this cannot improve is returned unchanged so the real
    driver error surfaces rather than one of our own.
    """
    if not _is_postgres(url):
        return url
    try:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        parts = urlsplit(url)
        # Pin psycopg3 when no driver was named.
        scheme = parts.scheme
        if scheme in ("postgres", "postgresql"):
            scheme = f"{scheme}+psycopg"

        removed = [
            (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() in _STRIPPED_PARAMS
        ]
        kept = [
            (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in _STRIPPED_PARAMS
        ]
        # If nothing was dropped, keep the query string byte-identical rather
        # than re-encoding it and quietly reordering the parameters.
        query = parts.query if not removed else urlencode(kept)
        return urlunsplit((scheme, parts.netloc, parts.path, query, parts.fragment))
    except Exception:
        return url


_db_url = normalise_database_url(settings.database_url)

# connect_timeout keeps a dead database from stalling the caller; without it a
# down server turns every write into a long, silent hang.
_connect_args: dict = {}
if _is_postgres(_db_url):
    _connect_args["connect_timeout"] = int(settings.db_connect_timeout_s)
    # Disable psycopg3's automatic prepared statements. Transaction-pooling
    # proxies — PgBouncer in transaction mode (Supabase, RDS Proxy, Azure, most
    # managed Postgres) — do not keep session state between transactions, so a
    # named prepared statement prepared on one connection is unknown on the next
    # and the server rejects it with `DuplicatePreparedStatement`. The URLs most
    # likely to be pooled are exactly the ones carrying `?pgbouncer=true`, which
    # the normaliser above strips; the cost of turning this off is one extra parse
    # per statement, and the benefit is that the same configuration works
    # against both a plain server and a pooled one.
    _connect_args["prepare_threshold"] = None

engine = create_engine(
    _db_url,
    pool_pre_ping=True,       # reconnect after DB restart
    pool_size=5,
    max_overflow=10,
    echo=False,
    connect_args=_connect_args,
)


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context-managed database session.

    Usage::

        with get_session() as session:
            session.add(obj)
            session.commit()

    Raises DatabaseUnavailable immediately while the degraded-mode breaker is
    open, rather than blocking on a connect attempt to a server already known
    to be unreachable.
    """
    if is_degraded():
        raise DatabaseUnavailable(degraded_reason() or "database unreachable")

    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


class DatabaseUnavailable(RuntimeError):
    """Raised when the breaker is open, i.e. the database is believed down."""


def create_all_tables() -> None:
    """Create all tables defined in models.py if they do not already exist."""
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        mark_unavailable(str(exc).splitlines()[0])
        raise DatabaseUnavailable(str(exc).splitlines()[0]) from exc
    mark_available()


def drop_all_tables() -> None:
    """Drop ALL tables — use only in tests or initial setup."""
    Base.metadata.drop_all(bind=engine)
