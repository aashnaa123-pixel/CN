"""
netrescue/security/tls.py

mTLS context construction (PRD §9, rule 1).

Uses Python's stdlib `ssl` module — never a hand-rolled implementation, per
PRD §9 rule 1. The job here is to turn the configured certificate paths into a
working SSLContext and to fail loudly when mutual authentication is required but
the material is missing.

Unknown nodes must be rejected by certificate identity, not by IP address
(PRD §9 rule 2): `check_hostname` is on and the CA is the trust anchor, so a
node presenting a certificate signed by an unknown CA is rejected at handshake.
"""

from __future__ import annotations

import logging
import os
import ssl
from dataclasses import dataclass
from pathlib import Path

from netrescue.core.config import settings

log = logging.getLogger(__name__)


class TLSUnavailable(RuntimeError):
    """Raised when TLS material is missing or unusable."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - formatting only
        base = super().__str__()
        return f"{base}\n  Hint: {self.hint}" if self.hint else base


@dataclass
class TLSMaterial:
    """Resolved certificate paths plus whether they exist."""

    ca_cert: str
    cert: str
    key: str
    present: bool

    def missing(self) -> list[str]:
        out = []
        for label, path in (("ca", self.ca_cert), ("cert", self.cert), ("key", self.key)):
            if not path or not Path(path).exists():
                out.append(label)
        return out


def material_from_settings() -> TLSMaterial:
    """Read the configured certificate paths and report whether they exist."""
    ca, cert, key = settings.tls_ca_cert, settings.tls_node_cert, settings.tls_node_key
    m = TLSMaterial(ca, cert, key, present=False)
    m.present = not m.missing()
    return m


def require_material(material: TLSMaterial | None = None) -> TLSMaterial:
    """Raise TLSUnavailable unless all three files are present and readable."""
    material = material or material_from_settings()
    missing = material.missing()
    if missing:
        raise TLSUnavailable(
            f"TLS material missing: {', '.join(missing)}",
            "Generate a local CA and a node certificate under ./certs, or set "
            "TLS_CA_CERT / TLS_NODE_CERT / TLS_NODE_KEY in .env. For local "
            "experiments without TLS, set ENABLE_SECURITY=false.",
        )
    for path in (material.ca_cert, material.cert, material.key):
        if not os.access(path, os.R_OK):
            raise TLSUnavailable(
                f"cannot read TLS material: {path}",
                "Check filesystem permissions on the certificate files.",
            )
    return material


# ---------------------------------------------------------------------------
# Contexts
# ---------------------------------------------------------------------------


def server_context(
    material: TLSMaterial | None = None,
    require_client_cert: bool = True,
    minimum_version: ssl.TLSVersion = ssl.TLSVersion.TLSv1_2,
) -> ssl.SSLContext:
    """
    Build a server SSLContext that requires a client certificate.

    With `require_client_cert=True` (the default, and what PRD §9 demands) the
    handshake fails unless the peer presents a certificate chaining to the
    configured CA. That is the "unknown node joins" control.
    """
    material = require_material(material)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = minimum_version
    ctx.load_cert_chain(certfile=material.cert, keyfile=material.key)

    try:
        ctx.load_verify_locations(cafile=material.ca_cert)
    except ssl.SSLError as exc:
        raise TLSUnavailable(
            f"could not load CA certificate {material.ca_cert}: {exc}",
            "Confirm the CA file is a PEM bundle.",
        ) from exc

    ctx.verify_mode = ssl.CERT_REQUIRED if require_client_cert else ssl.CERT_NONE
    # Identity comes from the certificate chain, never from the peer address.
    ctx.check_hostname = False  # servers do not verify their own name here
    log.info(
        "[tls] server context ready (client certs %s)",
        "required" if require_client_cert else "optional",
    )
    return ctx


def client_context(
    material: TLSMaterial | None = None,
    verify_peer: bool = True,
    minimum_version: ssl.TLSVersion = ssl.TLSVersion.TLSv1_2,
) -> ssl.SSLContext:
    """
    Build a client SSLContext presenting the node certificate.

    Verifies the server against the CA when `verify_peer` is set, so a node will
    not happily connect to an impostor control plane.
    """
    material = require_material(material)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = minimum_version
    ctx.load_cert_chain(certfile=material.cert, keyfile=material.key)

    if verify_peer:
        try:
            ctx.load_verify_locations(cafile=material.ca_cert)
        except ssl.SSLError as exc:
            raise TLSUnavailable(
                f"could not load CA certificate {material.ca_cert}: {exc}",
                "Confirm the CA file is a PEM bundle.",
            ) from exc
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = True
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        log.warning("[tls] client context built WITHOUT peer verification.")

    log.info("[tls] client context ready (verify_peer=%s)", verify_peer)
    return ctx


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def describe_peer(ssl_object: ssl.SSLObject | ssl.SSLSocket) -> dict[str, str | None]:
    """
    Extract the peer's identity from an established TLS session.

    Only non-secret fields are returned — never the key, never a private
    attribute of the socket. Safe to log and to surface on the audit screen.
    """
    try:
        cert = ssl_object.getpeercert()
    except ValueError:
        return {"subject": None, "issuer": None, "not_after": None}

    def flatten(entries) -> str | None:
        if not entries:
            return None
        parts = []
        for rdn in entries:
            for k, v in rdn:
                parts.append(f"{k}={v}")
        return ",".join(parts)

    return {
        "subject": flatten(cert.get("subject")),
        "issuer": flatten(cert.get("issuer")),
        "not_after": cert.get("notAfter"),
    }


def tls_status() -> dict:
    """Capability report for the API (PRD §8.2 security/audit screen)."""
    material = material_from_settings()
    return {
        "enabled": bool(settings.enable_security),
        "material_present": material.present,
        "missing": material.missing(),
        "ca_cert": material.ca_cert,
        "cert": material.cert,
        "key": material.key,
    }