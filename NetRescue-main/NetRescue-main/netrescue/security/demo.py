"""
netrescue/security/demo.py

The route allow-list for hosted public-demo mode (PUBLIC_DEMO=true).

A browser cannot hold a secret, so a public site cannot prove the operator role
with API_AUTH_TOKEN. Instead the server grants the operator role without a token
to a short, explicit list of routes whose effect is bounded and confined to the
API process: pure simulations and in-process labs. Anything that touches the host
(real probes, tc/netem, the recovery lab's sockets, deleting history, the real
loopback socket experiment) is absent from this list and still needs the token.

Matching is on the path only and on whole path segments, never on a substring.
"""

from __future__ import annotations

#: (method, path prefix). A prefix matches the path itself or any sub-path.
DEMO_OPERATOR_ROUTES: tuple[tuple[str, str], ...] = (
    # Networking labs — algorithmic simulations. /tcp-udp/real is deliberately absent.
    ("POST", "/api/labs/routing"),
    ("POST", "/api/labs/tcp"),
    ("POST", "/api/labs/shaping"),
    ("POST", "/api/labs/tcp-udp/simulate"),
    ("POST", "/api/labs/integration"),
    # Diagnostics lab — seeded fault scenarios and model sweeps only.
    ("POST", "/api/diagnostics/faults"),
    ("POST", "/api/diagnostics/hysteresis-sweep"),
    ("POST", "/api/diagnostics/scheduler-comparison"),
    # Traffic lab, multipath transfer engine and hysteresis playground (in-process).
    ("POST", "/api/traffic"),
    ("POST", "/api/xfer"),
    ("POST", "/api/playground"),
    ("PUT", "/api/playground"),
)


def is_demo_operator_route(method: str, path: str) -> bool:
    method = method.upper()
    for m, prefix in DEMO_OPERATOR_ROUTES:
        if m == method and (path == prefix or path.startswith(prefix + "/")):
            return True
    return False
