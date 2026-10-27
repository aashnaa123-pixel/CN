"""
netrescue.security — Authentication, authorisation and audit (PRD §9).

Four concerns, kept separate on purpose:

  hmac_auth : message authenticity, integrity, replay protection
  tls       : mTLS contexts built on the stdlib ssl module
  authz     : role-based authorisation for the control API
  audit     : the administrative trail, with secret scrubbing

Rule 1 of PRD §9 — no custom encryption. HMAC is used for authenticity and
integrity only; confidentiality comes from TLS.
"""

from netrescue.security.audit import Auditor, AuditRecord, scrub
from netrescue.security.authz import (
    ROLE_PERMISSIONS,
    AuthorizationError,
    Authorizer,
    Permission,
    Principal,
    Role,
)
from netrescue.security.hmac_auth import (
    Authenticator,
    ReplayGuard,
    canonical_body,
    generate_key,
    load_key,
    sign,
    verify,
)
from netrescue.security.tls import (
    TLSUnavailable,
    client_context,
    describe_peer,
    material_from_settings,
    server_context,
    tls_status,
)

__all__ = [
    # hmac_auth
    "Authenticator",
    "ReplayGuard",
    "canonical_body",
    "generate_key",
    "load_key",
    "sign",
    "verify",
    # tls
    "TLSUnavailable",
    "client_context",
    "server_context",
    "describe_peer",
    "material_from_settings",
    "tls_status",
    # authz
    "Authorizer",
    "AuthorizationError",
    "Permission",
    "Principal",
    "Role",
    "ROLE_PERMISSIONS",
    # audit
    "Auditor",
    "AuditRecord",
    "scrub",
]