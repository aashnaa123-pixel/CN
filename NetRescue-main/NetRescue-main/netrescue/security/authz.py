"""
netrescue/security/authz.py

Role-based authorisation for the control API (PRD §8.1, §9).

PRD §9 rule 3 requires three separated identities — node, operator and
read-only dashboard — with least-privilege database roles. Rule 2 is explicit
that reachability is not identity: being able to open a socket to this process
grants nothing.

Every state-changing endpoint requires the operator role. Read endpoints accept
dashboard or operator. Node identities may not change control-plane state at
all.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

log = logging.getLogger(__name__)


class Role(str, enum.Enum):
    """Separated identities (PRD §9 rule 3)."""

    READONLY = "readonly"
    OPERATOR = "operator"
    NODE = "node"


class Permission(str, enum.Enum):
    """Capabilities, mapped from the PRD §8.1 access-rule column."""

    READ = "read"
    WRITE = "write"
    APPLY_EMULATION = "apply_emulation"
    INJECT_FAULT = "inject_fault"
    ADMIN = "admin"


#: Least-privilege matrix. Node identities are deliberately excluded from every
#: write capability — a compromised node must not be able to reshape the network.
ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.READONLY: {Permission.READ},
    Role.OPERATOR: {
        Permission.READ,
        Permission.WRITE,
        Permission.APPLY_EMULATION,
        Permission.INJECT_FAULT,
    },
    Role.NODE: {Permission.READ},
}


class AuthorizationError(PermissionError):
    """Raised when a principal lacks a required permission."""

    def __init__(self, principal: str, permission: Permission, action: str = ""):
        self.principal = principal
        self.permission = permission
        self.action = action
        super().__init__(
            f"principal {principal!r} lacks permission {permission.value!r}"
            + (f" for {action!r}" if action else "")
        )


@dataclass
class Principal:
    """An authenticated caller."""

    name: str
    role: Role

    def has(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS.get(self.role, set())


@dataclass
class Authorizer:
    """
    Checks permissions and writes an audit entry for every decision.

    Every call is audited, allow or deny — PRD §9 requires unauthorised route
    changes to leave a visible trail.
    """

    auditor: Any = None  # netrescue.security.audit.Auditor

    def __init__(self, auditor: Any = None) -> None:
        self.auditor = auditor

    # ------------------------------------------------------------------

    def check(self, principal: Principal, permission: Permission, action: str = "") -> bool:
        """Non-raising permission check. Always audits the outcome."""
        allowed = principal.has(permission)
        if self.auditor is not None:
            if allowed:
                self.auditor.allow(
                    principal.name,
                    action or permission.value,
                    principal.role.value,
                    {"permission": permission.value},
                )
            else:
                self.auditor.deny(
                    principal.name,
                    action or permission.value,
                    principal.role.value,
                    {"permission": permission.value, "reason": "insufficient_role"},
                )
        return allowed

    def require(self, principal: Principal, permission: Permission, action: str = "") -> None:
        """Raise AuthorizationError unless the principal holds the permission."""
        if not self.check(principal, permission, action):
            raise AuthorizationError(principal.name, permission, action)

    # ------------------------------------------------------------------

    def require_operator(self, principal: Principal, action: str = "") -> None:
        """Shorthand for the operator-only endpoints in PRD §8.1."""
        self.require(principal, Permission.WRITE, action)