"""apps/api/main.py — NetRescue FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from netrescue.core.config import settings
from netrescue.core.cors import install_cors, parse_cors_origins
from netrescue.core.db import DatabaseUnavailable, create_all_tables
from netrescue.core.models import Base

from apps.api.routers import (
    benchmarks,
    diagnostics,
    emulation,
    experiments,
    lab,
    labs,
    metrics,
    nodes,
    playground,
    recovery,
    system,
    traffic,
    transfers,
    xfer,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup / shutdown.

    Schema creation is best-effort. If PostgreSQL is not reachable the API still
    starts and serves every endpoint that does not need the database —
    benchmarks, traffic lab, recovery lab, sweeps, comparisons and /health.
    Database-backed routes fail individually until the server is up (PRD §2.2
    rule 3: visible degraded mode rather than a dead process).

    The Recovery Lab's live topology is started and stopped here so its sockets
    are released cleanly, and bound to the event broker so node and delivery
    events reach WebSocket subscribers.
    """
    log.info("NetRescue API starting — creating tables if needed.")
    try:
        create_all_tables()
        log.info("Database reachable; schema ensured.")
    except DatabaseUnavailable as exc:
        log.warning(
            "Database unavailable at startup (%s). Starting in DEGRADED mode — "
            "benchmark, traffic and recovery lab endpoints work; database-backed "
            "routes need PostgreSQL running.", exc,
        )

    # Wire the live lab to the event stream.
    from apps.api.runtime import lab_service, playground_service, transfer_lab
    from apps.api.routers.system import broker
    from netrescue.security.audit import default_auditor

    import asyncio

    lab_service.attach(broker)
    lab_service.set_loop(asyncio.get_running_loop())
    await lab_service.start()
    # The playground journals lab events as they happen, so its timeline shows
    # detector transitions and delivery confirmations rather than only the
    # steps the browser happened to click.
    lab_service.attach_observer(playground_service.observe)

    # Start the audit flusher so records reach PostgreSQL.
    auditor = default_auditor()

    yield

    # Drain anything still queued before the process goes away. A failure here
    # must not stop the server exiting — the records are already counted as lost
    # by the auditor, which is the honest outcome.
    try:
        auditor._stop.set()
        auditor.flush_now()
    except Exception as exc:  # pragma: no cover - shutdown path
        log.warning("audit flush on shutdown failed: %s", exc)

    await lab_service.stop()
    # Drop any uploaded bytes the transfer lab is still holding. Shutdown is the
    # other place, besides reset and expiry, where an upload stops existing.
    transfer_lab.reset()
    log.info("NetRescue API shutting down.")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="NetRescue API",
    version="1.0.0",
    description=(
        "Control plane for NetRescue — self-healing recovery (Feature A), "
        "multipath transfer (Feature B), Windows network diagnostics & "
        "benchmarking (Feature 3), and the networking labs (routing, TCP congestion, "
        "traffic shaping, TCP vs UDP)."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — an explicit allow-list, never a wildcard.
#
# CORS_ORIGINS lists the exact frontend origins (the deployed site); loopback
# origins are added on top for local development while CORS_ALLOW_LOOPBACK is
# true. A malformed list stops startup with a message naming the variable, because
# a wrong CORS policy otherwise surfaces in the browser as an opaque network error.
# Authentication is header-based (no cookies), so credentials are not enabled.
_cors = parse_cors_origins(settings.cors_origins)

install_cors(app, _cors, settings.cors_allow_loopback)
log.info(
    "CORS: origins=%s loopback=%s",
    ", ".join(_cors) or "(none)",
    settings.cors_allow_loopback,
)
if settings.public_demo:
    log.warning(
        "PUBLIC_DEMO is on: tokenless callers get the operator role on the simulation "
        "routes in netrescue.security.demo only."
    )

if settings.api_bind_host not in ("127.0.0.1", "localhost", "::1") and not settings.api_auth_token:
    log.warning(
        "API_BIND_HOST=%s is not loopback and API_AUTH_TOKEN is empty: any client that "
        "can reach this port can claim any role. Set API_AUTH_TOKEN and CORS_ORIGINS.",
        settings.api_bind_host,
    )


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


# Routers
app.include_router(nodes.router)
app.include_router(experiments.router)
app.include_router(transfers.router)
app.include_router(metrics.router)
app.include_router(recovery.router)
app.include_router(benchmarks.router)
app.include_router(emulation.router)
app.include_router(system.router)
app.include_router(lab.router)
app.include_router(playground.router)
app.include_router(xfer.router)
app.include_router(traffic.router)
app.include_router(diagnostics.router)
app.include_router(labs.router)


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
def health():
    """Sanitised health check — no secrets returned."""
    return {"status": "ok", "version": "1.0.0"}


@app.get("/ready", tags=["health"])
def ready():
    """Readiness check — verify DB connectivity."""
    from netrescue.core.db import get_session
    try:
        with get_session() as session:
            session.execute(__import__("sqlalchemy").text("SELECT 1"))
        return {"status": "ready"}
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=f"Database not ready: {exc}")
