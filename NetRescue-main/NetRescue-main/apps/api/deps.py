"""apps/api/deps.py — shared FastAPI dependencies."""

# NOTE: deliberately no `from __future__ import annotations` in this module.
#
# require_permission() returns a closure whose parameter annotations are
# Annotated[...] aliases defined here. With PEP 563 those annotations become
# strings, and pydantic then resolves them while inspecting the *consuming*
# module's globals — where PrincipalDep and AuthorizerDep do not exist. The
# result is a NameError at import time of the first router that uses
# RequireOperator. Real annotation objects keep resolution local to this module.

import hmac
from typing import Annotated, Generator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from netrescue.core.config import settings
from netrescue.core.db import get_session
from netrescue.security.audit import default_auditor
from netrescue.security.demo import is_demo_operator_route
from netrescue.security.authz import (
    AuthorizationError,
    Authorizer,
    Permission,
    Principal,
    Role,
)


def get_db() -> Generator[Session, None, None]:
    with get_session() as session:
        yield session


DbSession = Annotated[Session, Depends(get_db)]


def get_optional_db() -> Generator[Session | None, None, None]:
    """
    A session that yields None instead of raising when PostgreSQL is unreachable.

    Endpoints whose work is fundamentally local — running a transfer, computing
    a benchmark — can then still do their job in degraded mode and simply report
    that the result was not persisted. An endpoint that cannot function without
    the database should use `DbSession` and fail loudly instead.
    """
    from netrescue.core.db import DatabaseUnavailable

    try:
        session = next(get_db())
    except DatabaseUnavailable:
        yield None
        return
    try:
        yield session
    finally:
        session.close()


OptionalDbSession = Annotated[Session | None, Depends(get_optional_db)]


# ---------------------------------------------------------------------------
# Authentication / authorisation
# ---------------------------------------------------------------------------
#
# The prototype binds to loopback only (PRD §2.2 rule 4), so the principal is
# supplied by the caller. Replace `resolve_principal` with mTLS certificate
# extraction (netrescue.security.tls.describe_peer) when TLS is enabled — the
# rest of the stack is unchanged, because everything below depends only on the
# Principal object.


def resolve_principal(request: Request) -> Principal:
    """
    Determine the calling principal.

    Header names match the three identities PRD §9 rule 3 requires be kept
    separate. A missing header defaults to `readonly`, which can never write —
    so an unlabelled request is safe by default rather than privileged.
    """
    name = request.headers.get("X-NetRescue-Principal", "anonymous")
    raw_role = request.headers.get("X-NetRescue-Role", Role.READONLY.value).lower()
    try:
        role = Role(raw_role)
    except ValueError:
        role = Role.READONLY
    # With a token configured, a claimed role is only honoured when the caller
    # proves it; the headers alone are otherwise trivially forgeable.
    # Hosted demo: a caller with no proof may hold the operator role on the
    # simulation allow-list only (netrescue.security.demo). No other role is
    # ever granted this way.
    demo_grant = (
        settings.public_demo
        and role is Role.OPERATOR
        and is_demo_operator_route(request.method, request.url.path)
    )
    if settings.api_auth_token and role is not Role.READONLY:
        supplied = request.headers.get("X-NetRescue-Token", "")
        if not hmac.compare_digest(supplied.encode(), settings.api_auth_token.encode()):
            role = Role.OPERATOR if demo_grant else Role.READONLY
    elif settings.public_demo and role is not Role.READONLY and not demo_grant:
        role = Role.READONLY
    return Principal(name=name, role=role)


PrincipalDep = Annotated[Principal, Depends(resolve_principal)]


def get_authorizer() -> Authorizer:
    return Authorizer(auditor=default_auditor())


AuthorizerDep = Annotated[Authorizer, Depends(get_authorizer)]


def require_permission(permission: Permission, action: str = ""):
    """
    Build a type alias that enforces one permission.

    Returns ``Annotated[Principal, Depends(...)]`` rather than a bare callable,
    so it composes with `from __future__ import annotations` in the router
    modules. A plain function used as a parameter annotation is evaluated to a
    function object, which FastAPI does not recognise as a dependency — it
    treats it as an unknown query parameter and every request fails validation
    with 422. Annotated metadata survives the round-trip through the string
    annotation and keeps the Depends attached.

    Denials return 403 and are written to the audit trail, so an unauthorised
    route change leaves a visible record (PRD §9, §14).
    """

    def _dep(principal: PrincipalDep, authorizer: AuthorizerDep) -> Principal:
        try:
            authorizer.require(principal, permission, action)
        except AuthorizationError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        return principal

    return Annotated[Principal, Depends(_dep)]


def require_host_probe(principal: PrincipalDep, authorizer: AuthorizerDep) -> Principal:
    """
    Gate for probes that run on the API host's own network (ping, traceroute, DNS,
    adapter inspection).

    Locally that host is the user's machine, so a read-only caller may use them.
    On a public demo the host is the hosting provider's server: a probe there
    measures the wrong network and would let anonymous visitors aim ICMP, traceroute
    and DNS lookups at arbitrary targets from it. So in public-demo mode they need the
    operator role, which only a caller holding API_AUTH_TOKEN can have on these routes.
    """
    permission = Permission.WRITE if settings.public_demo else Permission.READ
    try:
        authorizer.require(principal, permission, "host-probe")
    except AuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"{exc}. Real network probes run on the machine hosting the API, not on "
                "your device; run NetRescue locally to measure your own network."
            ),
        ) from exc
    return principal


RequireHostProbe = Annotated[Principal, Depends(require_host_probe)]

RequireOperator = require_permission(Permission.WRITE, "control-plane-write")
RequireEmulation = require_permission(Permission.APPLY_EMULATION, "apply-emulation")
RequireRead = require_permission(Permission.READ, "read")