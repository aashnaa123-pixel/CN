"""
netrescue/core/cors.py

Parsing and validation of the browser-origin allow-list.

A CORS misconfiguration fails in the browser as an opaque network error, so the
list is validated at startup and a bad value stops the process with a message
that names the variable and the offending entry, instead of silently allowing
nothing (or, worse, everything).
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: Matches http://localhost:<port>, http://127.0.0.1:<port> and http://[::1]:<port>,
#: and nothing else. Covers every local Vite dev port without enumerating them.
LOOPBACK_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"


class CorsConfigError(ValueError):
    """CORS_ORIGINS holds a value that cannot be a browser origin."""


def parse_cors_origins(raw: str) -> list[str]:
    """
    Turn a comma-separated CORS_ORIGINS value into exact, normalised origins.

    An origin is ``scheme://host[:port]`` — no path, query or fragment. A single
    trailing slash is tolerated (it is the most common copy-paste slip) and
    removed. Wildcards are rejected outright: a wildcard origin is the shortcut
    this allow-list exists to avoid.
    """
    origins: list[str] = []
    for entry in (e.strip() for e in raw.split(",")):
        if not entry:
            continue
        if "*" in entry:
            raise CorsConfigError(
                f"CORS_ORIGINS entry {entry!r}: wildcards are not allowed; list each "
                "frontend origin explicitly, e.g. https://app.example.com"
            )
        parts = urlsplit(entry.rstrip("/") if entry.endswith("/") else entry)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise CorsConfigError(
                f"CORS_ORIGINS entry {entry!r}: expected scheme://host[:port] "
                "with http or https, e.g. https://app.example.com"
            )
        if parts.path or parts.query or parts.fragment or parts.username or parts.password:
            raise CorsConfigError(
                f"CORS_ORIGINS entry {entry!r}: an origin has no path, query, fragment "
                "or credentials; use only scheme://host[:port]"
            )
        try:
            port = parts.port
        except ValueError as exc:
            raise CorsConfigError(f"CORS_ORIGINS entry {entry!r}: invalid port") from exc
        host = parts.hostname.lower()
        if ":" in host:  # IPv6 literal
            host = f"[{host}]"
        origin = f"{parts.scheme}://{host}" + (f":{port}" if port else "")
        if origin not in origins:
            origins.append(origin)
    return origins


def install_cors(app, origins: list[str], allow_loopback: bool) -> None:
    """Attach the CORS middleware with an exact allow-list (never a wildcard)."""
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=LOOPBACK_ORIGIN_REGEX if allow_loopback else None,
        # Authentication is header-based (no cookies), so credentials stay off.
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-NetRescue-Principal",
            "X-NetRescue-Role",
            "X-NetRescue-Token",
        ],
        # Without these the browser hides the download's integrity headers from JS.
        expose_headers=[
            "Content-Disposition",
            "X-NetRescue-Source-Sha256",
            "X-NetRescue-Reconstructed-Sha256",
        ],
    )
