"""
netrescue/emulation/platform.py

Platform capability detection for `tc`/`netem` (PRD §2.2 rule 5, §9 rule 7).

Feature D emulation is a Linux kernel facility. Running NetRescue on
Windows/macOS is explicitly supported for the API, dashboard and controller —
but Feature C and D emulation are not, and must fail with an actionable
message rather than an opaque `FileNotFoundError: 'tc'` from subprocess.

This module answers three questions once, at import time:

  1. Is this a Linux kernel?
  2. Is the `tc` binary present?
  3. Do we have the privilege to actually change a qdisc?

Usage::

    from netrescue.emulation.platform import emulation_support, require_emulation

    require_emulation()          # raises EmulationUnavailable with a fix hint
    emulation_support().usable   # False on Windows, with .reason explaining why
"""

from __future__ import annotations

import functools
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field

#: Minimum useful capability name for changing a qdisc.
CAP_NET_ADMIN = "cap_net_admin"


class EmulationUnavailable(RuntimeError):
    """
    Raised when tc/netem emulation is requested on an unsupported host.

    Carries an actionable `hint` so the caller can surface something useful
    instead of a stack trace.
    """

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - formatting only
        base = super().__str__()
        return f"{base}\n  Hint: {self.hint}" if self.hint else base


@dataclass
class EmulationSupport:
    """Snapshot of this host's emulation capability."""

    is_linux: bool
    platform_name: str
    kernel_release: str
    tc_path: str | None
    has_cap_net_admin: bool | None
    usable: bool
    reason: str = ""
    hint: str = ""

    def to_dict(self) -> dict:
        return {
            "is_linux": self.is_linux,
            "platform": self.platform_name,
            "kernel": self.kernel_release,
            "tc_path": self.tc_path,
            "has_cap_net_admin": self.has_cap_net_admin,
            "usable": self.usable,
            "reason": self.reason,
            "hint": self.hint,
        }


@functools.lru_cache(maxsize=1)
def emulation_support() -> EmulationSupport:
    """Probe this host once and cache the result."""
    plat = platform.system()
    kernel = platform.release()
    is_linux = plat == "Linux"
    tc_path = shutil.which("tc") if is_linux else None

    if not is_linux:
        return EmulationSupport(
            is_linux=False,
            platform_name=plat,
            kernel_release=kernel,
            tc_path=None,
            has_cap_net_admin=False,
            usable=False,
            reason=f"tc/netem is a Linux kernel facility; this host is {plat}.",
            hint=(
                "Run Feature D under WSL2, a Linux VM, or a native Linux host. "
                "The API, dashboard and Feature A/C logic run fine on this machine; "
                "only kernel-level emulation is unavailable."
            ),
        )

    if not tc_path:
        return EmulationSupport(
            is_linux=True,
            platform_name=plat,
            kernel_release=kernel,
            tc_path=None,
            has_cap_net_admin=None,
            usable=False,
            reason="the `tc` binary was not found on PATH.",
            hint="Install iproute2 (Debian/Ubuntu: `apt install iproute2`).",
        )

    has_cap = _probe_cap_net_admin(tc_path)
    if has_cap is False:
        return EmulationSupport(
            is_linux=True,
            platform_name=plat,
            kernel_release=kernel,
            tc_path=tc_path,
            has_cap_net_admin=False,
            usable=False,
            reason=f"insufficient privilege to modify qdiscs (no {CAP_NET_ADMIN}).",
            hint=(
                "Grant the capability to the Python interpreter rather than running "
                "everything as root: `sudo setcap cap_net_admin+ep $(which python3)`. "
                "Node processes themselves should still run unprivileged (PRD §9 rule 7)."
            ),
        )

    return EmulationSupport(
        is_linux=True,
        platform_name=plat,
        kernel_release=kernel,
        tc_path=tc_path,
        has_cap_net_admin=has_cap,
        usable=True,
    )


def _probe_cap_net_admin(tc_path: str) -> bool | None:
    """
    Check whether we can actually change a qdisc, without changing one.

    `tc qdisc show` is read-only and succeeds unprivileged, so it cannot prove
    write access. Rather than mutating a real interface during a probe, inspect
    the effective capabilities of the current process where that information is
    available.

    Returns True / False / None (unknown).
    """
    # Linux: read /proc/self/status CapEff and test the CAP_NET_ADMIN bit.
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("CapEff:"):
                    bits = int(line.split()[1], 16)
                    return bool(bits & (1 << 12))  # CAP_NET_ADMIN == 12
    except OSError:
        pass

    # Fall back to asking tc about a loopback read; inconclusive.
    try:
        subprocess.run(
            [tc_path, "qdisc", "show", "dev", "lo"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return None  # tc works, but write privilege is unverified
    except Exception:
        return False


def require_emulation() -> EmulationSupport:
    """Raise EmulationUnavailable unless emulation is actually possible here."""
    support = emulation_support()
    if not support.usable:
        raise EmulationUnavailable(support.reason, support.hint)
    return support


def is_emulation_available() -> bool:
    """Boolean form of emulation_support().usable."""
    return emulation_support().usable