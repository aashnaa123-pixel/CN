"""
netrescue/diagnostics/probes.py — real Windows network diagnostics.

This module runs actual operating-system network commands and reads their real
output. It never invents a number: if a command is missing, times out or is
blocked, the caller gets a structured failure with a reason, not a plausible
placeholder.

What is real here
-----------------
    ping        the OS `ping` binary; latency and loss come from its output
    tracert     the Windows `tracert` binary; hops come from its output
    DNS         the OS resolver (``socket.getaddrinfo``) and, optionally,
                `nslookup` against a named server; duration is measured
    adapters    `Get-NetAdapter` / `Get-NetIPConfiguration` via PowerShell
    gateway     `Get-NetIPConfiguration`'s IPv4DefaultGateway

What is deliberately NOT done here
-----------------------------------
  * No interface is disabled, no route is changed, no firewall rule is added.
    Every command is a read-only query.
  * No privilege escalation. Basic diagnostics run unprivileged.
  * No shell. Commands are executed as argument arrays with ``shell=False`` and
    the target is validated first, so a target of ``8.8.8.8 & del *`` cannot
    become a command.
  * No claim that ICMP latency equals application latency, and no claim that a
    failed ping proves a link is down. Those caveats travel with every result
    in ``notes``.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import platform
import queue
import re
import shutil
import socket
import statistics
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

log = logging.getLogger(__name__)

#: Hard ceiling on how long any single subprocess may run, regardless of what
#: the caller asked for. Diagnostics must not pin a worker thread.
MAX_TIMEOUT_S = 60.0

#: A DNS label / hostname / IPv4 literal. Deliberately strict: the target is
#: passed as a single argv element (shell=False), and this is defence in depth.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)"
    r"(?!-)[A-Za-z0-9_-]{1,63}(?<!-)"
    r"(?:\.(?!-)[A-Za-z0-9_-]{1,63}(?<!-))*$"
)

#: What every ping/traceroute result says about its own meaning.
NETWORK_POLICY_NOTES = [
    "ICMP is frequently rate-limited, deprioritised or blocked outright by "
    "routers, firewalls and the target itself.",
    "A reply measures this host's ICMP round-trip time, not application "
    "latency; a failed probe does not by itself prove the link is down.",
    "Results depend on local Wi-Fi/Ethernet conditions, the current route, "
    "the operating system and the network policy in force right now.",
]


class ProbeError(RuntimeError):
    """
    A diagnostics operation that could not produce a real measurement.

    ``kind`` lets the API choose an HTTP status without guessing at the
    message:

        invalid_input   the caller's parameter is not usable        -> 422
        unsupported     the command/library is not present on this  -> 503
        timeout         the operation exceeded its bounded time      -> 504
        command_failed  the command ran and failed in a way that is  -> 502
                        not a normal "no reply"
    """

    def __init__(self, message: str, *, kind: str = "command_failed", hint: str = ""):
        super().__init__(message)
        self.kind = kind
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - formatting only
        base = super().__str__()
        return f"{base} Hint: {self.hint}" if self.hint else base


# ---------------------------------------------------------------------------
# Input validation and command execution
# ---------------------------------------------------------------------------


def is_windows() -> bool:
    return platform.system() == "Windows"


def validate_target(target: str) -> str:
    """
    Return a validated IPv4 address or hostname.

    Rejects anything that is not an IPv4 literal or a syntactically valid DNS
    hostname, and rejects empty or over-long input. IPv6 literals are accepted
    only in the ``[::1]``-style bracket form is *not* attempted here — the
    diagnostics targets are IPv4/hostname by design (the feature is specified
    for "an IPv4 address or hostname").
    """
    if target is None:
        raise ProbeError("No target was supplied.", kind="invalid_input")
    value = str(target).strip()
    if not value:
        raise ProbeError("The target is empty.", kind="invalid_input")
    if len(value) > 253:
        raise ProbeError("The target is longer than 253 characters.", kind="invalid_input")
    # Reject whitespace and shell metacharacters before they reach a process.
    if any(ch in value for ch in " \t\r\n;&|`$<>(){}[]'\"\\!*?~^%%"):
        raise ProbeError(
            f"{value!r} contains characters that are not valid in a hostname "
            "or IPv4 address.",
            kind="invalid_input",
        )

    try:
        ipaddress.IPv4Address(value)
        return value
    except ValueError:
        pass

    if not _HOSTNAME_RE.match(value):
        raise ProbeError(
            f"{value!r} is not a valid IPv4 address or hostname.",
            kind="invalid_input",
        )
    return value


def _bounded_timeout(timeout_s: float, *, floor: float = 0.2) -> float:
    try:
        value = float(timeout_s)
    except (TypeError, ValueError):
        raise ProbeError("timeout_s must be a number.", kind="invalid_input") from None
    if value <= 0:
        raise ProbeError("timeout_s must be greater than zero.", kind="invalid_input")
    return max(floor, min(value, MAX_TIMEOUT_S))


def _which(binary: str, *, hint: str = "") -> str:
    path = shutil.which(binary)
    if not path:
        raise ProbeError(
            f"The '{binary}' command was not found on this system.",
            kind="unsupported",
            hint=hint or f"Install a system that provides '{binary}', or use the "
                          f"simulated fault scenarios instead.",
        )
    return path


def run_command(
    argv: Sequence[str],
    timeout_s: float,
    *,
    hint: str = "",
) -> subprocess.CompletedProcess[str]:
    """
    Run one command with no shell, a hard timeout, and no visible window.

    ``argv[0]`` must resolve on PATH. A timeout is reported as ``ProbeError``
    with kind ``timeout``; a non-zero exit is returned to the caller rather
    than raised, because `ping`/`tracert` use non-zero exits for "no reply",
    which is a legitimate measurement.
    """
    if not argv:
        raise ProbeError("No command was given.", kind="invalid_input")

    exe = _which(argv[0], hint=hint)
    full = [exe, *argv[1:]]
    timeout = _bounded_timeout(timeout_s)

    creationflags = 0
    if is_windows():
        # Keep the console window from flashing on every probe.
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        return subprocess.run(
            full,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            check=False,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(
            f"'{argv[0]}' did not finish within {timeout:.1f}s.",
            kind="timeout",
            hint="Increase the timeout, or pick a target that answers faster.",
        ) from exc
    except OSError as exc:
        raise ProbeError(
            f"'{argv[0]}' could not be started: {exc}",
            kind="command_failed",
        ) from exc


# ---------------------------------------------------------------------------
# Ping
# ---------------------------------------------------------------------------

#: `time=12ms` / `time<1ms` (Windows) and `time=12.3 ms` (iputils/macOS).
_PING_TIME_RE = re.compile(r"time[=<]\s*([0-9]+(?:\.[0-9]+)?)\s*ms", re.IGNORECASE)
_PING_SUBMS_RE = re.compile(r"time<\s*1\s*ms", re.IGNORECASE)
#: `0% loss` (Windows: "Lost = 0 (0% loss),") and POSIX "0% packet loss".
_PING_LOSS_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)%\s*(?:packet\s*)?loss", re.IGNORECASE)
#: Windows summary: "Minimum = 0ms, Maximum = 1ms, Average = 0ms".
_PING_WIN_SUMMARY_RE = re.compile(
    r"Minimum\s*=\s*([0-9.]+)\s*ms.*?Maximum\s*=\s*([0-9.]+)\s*ms.*?"
    r"Average\s*=\s*([0-9.]+)\s*ms",
    re.IGNORECASE | re.DOTALL,
)
#: POSIX summary: "rtt min/avg/max/mdev = 0.031/0.042/0.058/0.010 ms".
_PING_POSIX_SUMMARY_RE = re.compile(
    r"=\s*([0-9.]+)/([0-9.]+)/([0-9.]+)(?:/([0-9.]+))?\s*ms",
)


@dataclass
class ProbeResponse:
    """A completed ping probe, parseable without running anything."""

    target: str
    sent: int
    received: int
    samples_ms: list[float]
    loss_rate: float
    sub_millisecond_samples: int = 0
    duration_s: float = 0.0
    command: list[str] = field(default_factory=list)
    raw: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def reachable(self) -> bool:
        return self.received > 0

    @property
    def min_ms(self) -> float | None:
        return round(min(self.samples_ms), 3) if self.samples_ms else None

    @property
    def max_ms(self) -> float | None:
        return round(max(self.samples_ms), 3) if self.samples_ms else None

    @property
    def avg_ms(self) -> float | None:
        if not self.samples_ms:
            return None
        return round(statistics.fmean(self.samples_ms), 3)

    @property
    def stddev_ms(self) -> float | None:
        """Population standard deviation of the per-probe RTTs."""
        if len(self.samples_ms) < 2:
            return None
        return round(statistics.pstdev(self.samples_ms), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "command": self.command,
            "sent": self.sent,
            "received": self.received,
            "loss_rate": round(self.loss_rate, 6),
            "loss_pct": f"{self.loss_rate * 100:.1f}%",
            "samples_ms": [round(s, 3) for s in self.samples_ms],
            "min_ms": self.min_ms,
            "avg_ms": self.avg_ms,
            "max_ms": self.max_ms,
            "stddev_ms": self.stddev_ms,
            "sub_millisecond_samples": self.sub_millisecond_samples,
            "duration_s": round(self.duration_s, 3),
            "units": "ms",
            "reachable": self.reachable,
            "timestamp": time.time(),
            "notes": self.notes,
        }


def parse_ping_output(target: str, text: str) -> ProbeResponse:
    """
    Parse `ping` output into a ProbeResponse.

    Works for both Windows and POSIX output. Sub-millisecond replies (``<1ms``)
    are recorded as ``0.0`` and counted in ``sub_millisecond_samples`` rather
    than being rounded up into a number the tool never produced — so on a
    sub-millisecond path the reported average is a lower bound and the result
    says so.
    """
    samples: list[float] = []
    sub_ms = 0
    for line in text.splitlines():
        if _PING_SUBMS_RE.search(line):
            samples.append(0.0)
            sub_ms += 1
            continue
        match = _PING_TIME_RE.search(line)
        if match:
            samples.append(float(match.group(1)))

    loss_match = _PING_LOSS_RE.search(text)
    loss_rate = float(loss_match.group(1)) / 100.0 if loss_match else 0.0

    # Prefer the tool's own sent/received counts where present.
    sent = len(samples)
    received = len(samples)
    win = re.search(r"Sent\s*=\s*(\d+),\s*Received\s*=\s*(\d+)", text, re.IGNORECASE)
    if win:
        sent, received = int(win.group(1)), int(win.group(2))
    else:
        posix = re.search(
            r"(\d+)\s+packets?\s+transmitted,\s+(\d+)\s+(?:packets\s+)?received",
            text,
            re.IGNORECASE,
        )
        if posix:
            sent, received = int(posix.group(1)), int(posix.group(2))
        else:
            sent = max(sent, 1) if samples else 0
            received = len(samples)

    notes = list(NETWORK_POLICY_NOTES)
    if sub_ms:
        notes.append(
            f"{sub_ms} reply(ies) were reported as '<1ms' and recorded as 0 ms, "
            "so the average is a lower bound on this sub-millisecond path."
        )
    if received == 0:
        notes.append(
            "No reply was received. This can mean the host is down, the route "
            "is gone, ICMP is blocked, or the target is rate-limiting probes."
        )

    return ProbeResponse(
        target=target,
        sent=sent,
        received=received,
        samples_ms=samples,
        loss_rate=loss_rate,
        sub_millisecond_samples=sub_ms,
        notes=notes,
    )


def ping(
    target: str,
    *,
    count: int = 4,
    timeout_s: float = 5.0,
    size_bytes: int | None = None,
) -> ProbeResponse:
    """
    Ping an IPv4 address or hostname using the OS `ping` binary.

    ``count`` probes are sent, each waiting up to ``timeout_s``. Returns a
    ProbeResponse whether or not any reply arrives — a zero-reply result is a
    real measurement of loss, not an error.
    """
    host = validate_target(target)
    count = int(count)
    if not 1 <= count <= 100:
        raise ProbeError("count must be between 1 and 100.", kind="invalid_input")
    timeout = _bounded_timeout(timeout_s)

    if is_windows():
        per_probe_ms = int(timeout * 1000)
        argv = ["ping", "-n", str(count), "-w", str(per_probe_ms)]
        if size_bytes is not None:
            size = int(size_bytes)
            if not 8 <= size <= 65500:
                raise ProbeError("size_bytes must be between 8 and 65500.", kind="invalid_input")
            argv += ["-l", str(size)]
        argv.append(host)
    else:
        argv = ["ping", "-c", str(count), "-W", str(max(1, int(round(timeout))))]
        if size_bytes is not None:
            argv += ["-s", str(int(size_bytes))]
        argv.append(host)

    started = time.perf_counter()
    completed = run_command(
        argv,
        # Whole-command budget: one timeout per probe, plus a small margin.
        timeout_s=timeout * count + 2.0,
        hint="On Windows, `ping` is part of the OS. On Linux, install iputils-ping.",
    )
    duration = time.perf_counter() - started
    output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")

    response = parse_ping_output(host, output)
    response.duration_s = duration
    response.command = argv

    # A completely empty stdout with a failure is a real command error, not
    # just "no reply" — surface it rather than pretending it was measured.
    if not output.strip():
        raise ProbeError(
            f"'ping {host}' produced no output (exit {completed.returncode}).",
            kind="command_failed",
            hint="The command ran but printed nothing; retry or check the target.",
        )
    return response


# ---------------------------------------------------------------------------
# Traceroute (Windows `tracert`)
# ---------------------------------------------------------------------------

_HOP_LINE_RE = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")
_TIME_TOKEN_RE = re.compile(r"^(<\s*)?([0-9]+(?:\.[0-9]+)?)\s*ms$", re.IGNORECASE)

#: Windows tracert: `1    <1 ms    <1 ms    <1 ms  192.168.1.1`
_WIN_HOP_RE = re.compile(
    r"^\s*(\d+)\s+"
    r"(<\s*[0-9.]+\s*ms|[0-9.]+\s*ms|\*)\s+"
    r"(<\s*[0-9.]+\s*ms|[0-9.]+\s*ms|\*)\s+"
    r"(<\s*[0-9.]+\s*ms|[0-9.]+\s*ms|\*)\s+"
    r"(.+?)\s*$"
)


def _parse_time_token(token: str) -> tuple[float | None, bool]:
    """Return (ms, is_sub_millisecond). ``*`` gives (None, False)."""
    token = token.strip()
    if token == "*":
        return None, False
    match = _TIME_TOKEN_RE.match(token.replace(" ", ""))
    if not match:
        # Tokens sometimes arrive as "<1ms" without the space.
        match = re.match(r"^(<\s*)?([0-9.]+)ms$", token, re.IGNORECASE)
        if not match:
            return None, False
    sub = bool(match.group(1))
    value = 0.0 if sub else float(match.group(2))
    return value, sub


def parse_tracert_output(target: str, text: str, *, max_hops: int) -> dict[str, Any]:
    """Parse Windows `tracert -d` output into a list of hops."""
    hops: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = _WIN_HOP_RE.match(line)
        if not match:
            continue
        index = int(match.group(1))
        if index < 1 or index > max_hops:
            continue
        tokens = [match.group(2), match.group(3), match.group(4)]
        rtts: list[float | None] = []
        sub_ms = False
        for token in tokens:
            value, sub = _parse_time_token(token)
            rtts.append(value)
            sub_ms = sub_ms or sub
        node = match.group(5).strip()
        hops.append({
            "hop": index,
            "node": node,
            "rtts_ms": rtts,
            "rtt_ms": next((r for r in rtts if r is not None), None),
            "timed_out": all(r is None for r in rtts),
            "sub_millisecond": sub_ms,
        })
    return {"target": target, "hops": hops, "hop_count": len(hops),
            "max_hops": max_hops, "units": "ms"}


def traceroute(
    target: str,
    *,
    max_hops: int = 30,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    """
    Trace the route to ``target`` with the Windows `tracert` binary.

    Hops that do not answer appear as ``timed_out: true`` rather than being
    dropped, because a silent hop is itself an observation.
    """
    host = validate_target(target)
    max_hops = int(max_hops)
    if not 1 <= max_hops <= 64:
        raise ProbeError("max_hops must be between 1 and 64.", kind="invalid_input")
    timeout = _bounded_timeout(timeout_s)

    if is_windows():
        per_probe_ms = int(timeout * 1000)
        argv = ["tracert", "-d", "-h", str(max_hops), "-w", str(per_probe_ms), host]
        # Budget grows per hop; cap it so a black-holed target cannot run away.
        budget = min(MAX_TIMEOUT_S, 5.0 + timeout * max_hops * 0.6)
    else:
        argv = ["traceroute", "-n", "-m", str(max_hops), "-w", str(max(1, int(timeout))), host]
        budget = min(MAX_TIMEOUT_S, 5.0 + timeout * max_hops * 0.6)

    started = time.perf_counter()
    completed = run_command(argv, timeout_s=budget)
    duration = time.perf_counter() - started

    if is_windows():
        parsed = parse_tracert_output(host, completed.stdout or "", max_hops=max_hops)
    else:
        parsed = _parse_posix_traceroute(host, completed.stdout or "", max_hops=max_hops)

    parsed["duration_s"] = round(duration, 3)
    parsed["command"] = argv
    parsed["timestamp"] = time.time()
    parsed["notes"] = list(NETWORK_POLICY_NOTES) + [
        "tracert does not prove a hop is the cause of a problem; routers may "
        "deprioritise or filter the probe without affecting forwarded traffic.",
    ]
    if completed.returncode != 0 and not parsed["hops"]:
        parsed["error"] = (
            (completed.stderr or "").strip()
            or f"tracert exited {completed.returncode} without listing any hop."
        )
    return parsed


def _parse_posix_traceroute(target: str, text: str, *, max_hops: int) -> dict[str, Any]:
    """Minimal POSIX `traceroute -n` parser, so the module is not Windows-only."""
    hops: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = re.match(r"^\s*(\d+)\s+(\S+)\s+(.*)$", line)
        if not match:
            continue
        index = int(match.group(1))
        if index < 1 or index > max_hops:
            continue
        node = match.group(2)
        rtt_tokens = re.findall(r"([0-9.]+)\s*ms", match.group(3))
        rtts = [float(v) for v in rtt_tokens]
        hops.append({
            "hop": index,
            "node": node,
            "rtts_ms": rtts,
            "rtt_ms": rtts[0] if rtts else None,
            "timed_out": not rtts,
            "sub_millisecond": False,
        })
    return {"target": target, "hops": hops, "hop_count": len(hops),
            "max_hops": max_hops, "units": "ms"}


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------


def _resolve_in_thread(name: str, timeout_s: float) -> list[tuple[int, str]]:
    """
    Run ``socket.getaddrinfo`` with a bounded wait.

    ``getaddrinfo`` has no timeout of its own; on a black-holed resolver it can
    block for tens of seconds. A daemon thread plus a queue bounds the wait
    without the caller having to trust the OS resolver.
    """
    result: queue.Queue[Any] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            infos = socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)
            seen: list[tuple[int, str]] = []
            for family, _type, _proto, _canon, sockaddr in infos:
                address = sockaddr[0]
                item = (family, address)
                if item not in seen:
                    seen.append(item)
            result.put(seen)
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the caller
            result.put(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        outcome = result.get(timeout=timeout_s)
    except queue.Empty:
        raise ProbeError(
            f"DNS resolution of {name!r} did not complete within {timeout_s:.1f}s.",
            kind="timeout",
            hint="The configured resolver may be unreachable. Try another network "
                 "or a different resolver.",
        ) from None
    if isinstance(outcome, Exception):
        raise ProbeError(
            f"DNS resolution of {name!r} failed: {outcome}",
            kind="command_failed",
            hint="Check the hostname, and whether this machine's resolver is reachable.",
        )
    return outcome


def dns_lookup(
    name: str,
    *,
    server: str | None = None,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    """
    Resolve ``name`` and report the addresses and how long the lookup took.

    Uses the operating system resolver by default. When ``server`` is given it
    asks that server specifically via ``nslookup``, so the answer is attributed
    to the resolver that produced it.
    """
    host = validate_target(name)
    timeout = _bounded_timeout(timeout_s)

    if server:
        return _nslookup(host, validate_target(server), timeout)

    started = time.perf_counter()
    infos = _resolve_in_thread(host, timeout)
    duration_ms = (time.perf_counter() - started) * 1000.0

    ipv4 = [addr for family, addr in infos if family == socket.AF_INET]
    ipv6 = [addr for family, addr in infos if family == socket.AF_INET6]
    return {
        "name": host,
        "resolver": "operating system resolver (socket.getaddrinfo)",
        "addresses": ipv4 + ipv6,
        "ipv4": ipv4,
        "ipv6": ipv6,
        "duration_ms": round(duration_ms, 3),
        "units": "ms",
        "resolved": bool(infos),
        "timestamp": time.time(),
        "notes": [
            "Resolution time is measured locally and includes cache hits, so a "
            "second lookup of the same name is usually faster.",
            "Addresses shown are what this machine will use; a different "
            "resolver may return different answers.",
        ],
    }


def _nslookup(name: str, server: str, timeout_s: float) -> dict[str, Any]:
    argv = ["nslookup", name, server]
    started = time.perf_counter()
    completed = run_command(
        argv,
        timeout_s=timeout_s,
        hint="On Windows, `nslookup` is part of the OS.",
    )
    duration_ms = (time.perf_counter() - started) * 1000.0
    text = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")

    addresses: list[str] = []
    # In nslookup output the answer addresses follow the blank line.
    answer_section = text.split("\n\n", 1)[1] if "\n\n" in text else text
    for line in answer_section.splitlines():
        match = re.match(r"^\s*(?:Address|Addresses|Address\s*\d*)\s*:?\s*(.+)$", line, re.IGNORECASE)
        if not match:
            continue
        for candidate in match.group(1).split():
            try:
                ipaddress.ip_address(candidate)
                addresses.append(candidate)
            except ValueError:
                continue
    # De-duplicate, preserving order.
    deduped = list(dict.fromkeys(addresses))
    return {
        "name": name,
        "resolver": server,
        "addresses": deduped,
        "ipv4": [a for a in deduped if ":" not in a],
        "ipv6": [a for a in deduped if ":" in a],
        "duration_ms": round(duration_ms, 3),
        "units": "ms",
        "resolved": bool(deduped),
        "command": argv,
        "timestamp": time.time(),
        "notes": [
            f"The answer came from {server}, not necessarily this machine's "
            "default resolver.",
        ],
    }


# ---------------------------------------------------------------------------
# Adapters and IP configuration (Windows PowerShell)
# ---------------------------------------------------------------------------


def _powershell(script: str, timeout_s: float) -> str:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        raise ProbeError(
            "PowerShell was not found, so adapter information is unavailable.",
            kind="unsupported",
            hint="Get-NetAdapter/Get-NetIPConfiguration need Windows PowerShell. "
                 "On a non-Windows host this diagnostic is not supported.",
        )
    completed = run_command(
        [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        timeout_s=timeout_s,
    )
    if completed.returncode != 0 and not (completed.stdout or "").strip():
        raise ProbeError(
            f"PowerShell exited {completed.returncode}: "
            f"{(completed.stderr or '').strip()[:200]}",
            kind="command_failed",
            hint="The Get-Net* cmdlets require Windows 8+/Server 2012 or later.",
        )
    return (completed.stdout or "").strip()


def _as_list(value: Any) -> list[dict[str, Any]]:
    """PowerShell emits a bare object for one result and an array for many."""
    if value is None or value == "":
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


#: Project to plain scalars *inside* PowerShell. Selecting properties off a CIM
#: instance and piping it to ConvertTo-Json serialises the whole WMI class graph
#: (megabytes of metadata, and the cmdlet then times out); building a
#: [pscustomobject] emits a small, stable document instead.
ADAPTER_SCRIPT = (
    "Get-NetAdapter | ForEach-Object { "
    "[pscustomobject]@{ "
    "Name=$_.Name; Description=$_.InterfaceDescription; Status=$_.Status; "
    "LinkSpeed=$_.LinkSpeed; MacAddress=$_.MacAddress; IfIndex=$_.ifIndex } } | "
    "ConvertTo-Json -Depth 3 -Compress"
)

#: `Get-NetIPConfiguration` itself takes ~15 s on a machine with virtual
#: adapters (it resolves the whole interface graph). These three cmdlets answer
#: the same questions in ~6 s and are combined into one process launch.
IPCONFIG_SCRIPT = (
    "$ips = Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { "
    "[pscustomobject]@{ Alias=$_.InterfaceAlias; Index=$_.InterfaceIndex; "
    "IP=$_.IPAddress; Prefix=$_.PrefixLength } }; "
    "$routes = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' "
    "-ErrorAction SilentlyContinue | ForEach-Object { "
    "[pscustomobject]@{ Alias=$_.InterfaceAlias; Index=$_.ifIndex; NextHop=$_.NextHop } }; "
    "$dns = Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
    "ForEach-Object { [pscustomobject]@{ Alias=$_.InterfaceAlias; Servers=@($_.ServerAddresses) } }; "
    "[pscustomobject]@{ Addresses=@($ips); Routes=@($routes); Dns=@($dns) } | "
    "ConvertTo-Json -Depth 5 -Compress"
)

#: Adapter and IP-configuration queries take a few seconds (a fresh PowerShell
#: process each time). A short cache keeps the dashboard responsive without
#: hiding a real change for long; the TTL is stated in every response.
_CACHE_TTL_S = 15.0
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, producer: "Any") -> Any:
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry and now - entry[0] < _CACHE_TTL_S:
            value = entry[1]
            if isinstance(value, dict):
                value = {**value, "cached": True, "cache_age_s": round(now - entry[0], 2)}
            return value
    value = producer()
    # Timestamp when the query *finished*, not when it started; the query itself
    # can take several seconds, and a start timestamp would expire the entry
    # before it was ever useful.
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    if isinstance(value, dict):
        value = {**value, "cached": False, "cache_ttl_s": _CACHE_TTL_S}
    return value


def list_adapters(*, timeout_s: float = 15.0) -> dict[str, Any]:
    """
    Active network adapters and their link status via ``Get-NetAdapter``.

    Returns a structured ``available: false`` result on a host without the
    cmdlet rather than raising, because "this host cannot report adapters" is a
    valid answer to show the operator.
    """
    if not is_windows():
        return {
            "available": False,
            "reason": "Adapter enumeration here uses the Windows Get-NetAdapter cmdlet.",
            "hint": "Not supported on this host. Use a Windows 10/11 machine.",
            "adapters": [],
        }
    return _cached("adapters", lambda: _list_adapters_uncached(timeout_s))


def _list_adapters_uncached(timeout_s: float) -> dict[str, Any]:
    try:
        raw = _powershell(ADAPTER_SCRIPT, timeout_s)
        data = json.loads(raw) if raw else None
    except ProbeError as exc:
        return {"available": False, "reason": str(exc), "hint": exc.hint, "adapters": []}
    except json.JSONDecodeError as exc:
        return {
            "available": False,
            "reason": f"Get-NetAdapter output could not be parsed: {exc}",
            "hint": "Unexpected PowerShell output; check that the cmdlet exists.",
            "adapters": [],
        }

    adapters = []
    for item in _as_list(data):
        adapters.append({
            "name": item.get("Name"),
            "description": item.get("Description"),
            "status": item.get("Status"),
            "link_speed": item.get("LinkSpeed"),
            "mac_address": item.get("MacAddress"),
            "if_index": item.get("IfIndex"),
        })
    return {
        "available": True,
        "count": len(adapters),
        "up_count": sum(1 for a in adapters if str(a.get("status", "")).lower() == "up"),
        "adapters": adapters,
        "source": "Get-NetAdapter (PowerShell)",
        "timestamp": time.time(),
        "notes": ["Status is the adapter's link state, not whether the internet "
                  "is reachable through it."],
    }


def ip_configuration(*, timeout_s: float = 15.0) -> dict[str, Any]:
    """
    IPv4 addresses, DNS servers and the default gateway via
    ``Get-NetIPConfiguration``.
    """
    if not is_windows():
        return {
            "available": False,
            "reason": "IP configuration here uses the Windows Get-NetIPConfiguration cmdlet.",
            "hint": "Not supported on this host. Use a Windows 10/11 machine.",
            "interfaces": [],
        }
    return _cached("ip_configuration", lambda: _ip_configuration_uncached(timeout_s))


def _str_list(value: Any) -> list[str]:
    """PowerShell emits a scalar for one item and an array for several."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_str_list(item))
        return out
    if isinstance(value, dict):
        for key in ("IPAddress", "NextHop", "ServerAddresses", "Address"):
            if key in value:
                return _str_list(value[key])
        return []
    return [str(value)]


def _ip_configuration_uncached(timeout_s: float) -> dict[str, Any]:
    try:
        raw = _powershell(IPCONFIG_SCRIPT, timeout_s)
        data = json.loads(raw) if raw else None
    except ProbeError as exc:
        return {"available": False, "reason": str(exc), "hint": exc.hint, "interfaces": []}
    except json.JSONDecodeError as exc:
        return {
            "available": False,
            "reason": f"IP configuration output could not be parsed: {exc}",
            "hint": "Unexpected PowerShell output; check that the cmdlets exist.",
            "interfaces": [],
        }

    if not isinstance(data, dict):
        return {
            "available": False,
            "reason": "IP configuration output was not in the expected shape.",
            "hint": "The Get-NetIPAddress/Get-NetRoute cmdlets may be unavailable.",
            "interfaces": [],
        }

    dns_by_alias: dict[str, list[str]] = {}
    for entry in _as_list(data.get("Dns")):
        alias = str(entry.get("Alias"))
        dns_by_alias[alias] = list(dict.fromkeys(_str_list(entry.get("Servers"))))

    gateway_by_alias: dict[str, str] = {}
    default_gateway: str | None = None
    for entry in _as_list(data.get("Routes")):
        next_hop = (entry.get("NextHop") or "").strip()
        if not next_hop:
            continue
        gateway_by_alias[str(entry.get("Alias"))] = next_hop
        if default_gateway is None:
            default_gateway = next_hop

    interfaces: list[dict[str, Any]] = []
    for entry in _as_list(data.get("Addresses")):
        alias = str(entry.get("Alias"))
        address = entry.get("IP")
        if not address:
            continue
        interfaces.append({
            "alias": alias,
            "if_index": entry.get("Index"),
            "ipv4_addresses": [str(address)],
            "prefix_length": entry.get("Prefix"),
            "default_gateway": gateway_by_alias.get(alias),
            "dns_servers": dns_by_alias.get(alias, []),
        })

    # One row per interface alias, merging addresses that share a name.
    merged: dict[str, dict[str, Any]] = {}
    for item in interfaces:
        alias = item["alias"]
        if alias in merged:
            merged[alias]["ipv4_addresses"].extend(item["ipv4_addresses"])
            if not merged[alias]["dns_servers"] and item["dns_servers"]:
                merged[alias]["dns_servers"] = item["dns_servers"]
            if not merged[alias]["default_gateway"] and item["default_gateway"]:
                merged[alias]["default_gateway"] = item["default_gateway"]
        else:
            merged[alias] = item

    return {
        "available": True,
        "default_gateway": default_gateway,
        "interfaces": list(merged.values()),
        "source": "Get-NetIPAddress + Get-NetRoute + Get-DnsClientServerAddress (PowerShell)",
        "timestamp": time.time(),
        "notes": ["Only IPv4 addresses are listed."],
    }


def network_status() -> dict[str, Any]:
    """Adapters + IP configuration, composed for the Live Network Status panel."""
    from concurrent.futures import ThreadPoolExecutor

    # The two PowerShell queries are independent; run them together so the panel
    # waits once, not twice.
    with ThreadPoolExecutor(max_workers=2) as pool:
        adapters_future = pool.submit(list_adapters)
        ip_future = pool.submit(ip_configuration)
        adapters = adapters_future.result()
        ip_config = ip_future.result()
    return {
        "hostname": socket.gethostname(),
        "platform": platform.system(),
        "release": platform.release(),
        "adapters": adapters,
        "ip_configuration": ip_config,
        "default_gateway": ip_config.get("default_gateway"),
        "timestamp": time.time(),
    }


# ---------------------------------------------------------------------------
# Capability report
# ---------------------------------------------------------------------------


def suggested_target() -> str:
    """
    Pick a sensible default target for a one-click run.

    Preference order: the machine's own default gateway (a real, usually
    reachable LAN measurement), then loopback. Loopback always answers, so a
    user on a host with no LAN still gets a real measurement rather than an
    error.
    """
    try:
        gateway = ip_configuration().get("default_gateway")
        if gateway:
            validate_target(str(gateway))
            return str(gateway)
    except Exception:  # noqa: BLE001 - choosing a default must never raise
        pass
    return "127.0.0.1"


def probe_capabilities() -> dict[str, Any]:
    """
    Which real diagnostics are available on this host, and which are not.

    This is the honest boundary between "real measurement", "controlled
    simulation" and "unsupported on this platform". The API serves it verbatim.
    """
    available = {name: shutil.which(name) is not None for name in ("ping", "tracert", "traceroute", "nslookup")}
    powershell = shutil.which("powershell") or shutil.which("pwsh")

    real = {
        "ping": {"supported": is_windows() and available["ping"] or (not is_windows() and available["ping"]),
                 "command": "ping"},
        "traceroute": {
            "supported": (available["tracert"] if is_windows() else available["traceroute"]),
            "command": "tracert" if is_windows() else "traceroute",
        },
        "dns": {"supported": True, "command": "socket.getaddrinfo"},
        "nslookup": {"supported": available["nslookup"], "command": "nslookup"},
        "adapters": {"supported": bool(is_windows() and powershell),
                     "command": "Get-NetAdapter (PowerShell)"},
        "ip_configuration": {"supported": bool(is_windows() and powershell),
                             "command": "Get-NetIPAddress + Get-NetRoute + Get-DnsClientServerAddress (PowerShell)"},
    }
    return {
        "platform": platform.system(),
        "release": platform.release(),
        "is_windows": is_windows(),
        "real_measurements": real,
        "controlled_simulation": {
            "supported": True,
            "note": "Seeded fault scenarios and application benchmarks run "
                    "everywhere and are labelled simulated.",
        },
        "unsupported": {
            "kernel_emulation": {
                "supported": False,
                "reason": "Linux tc/netem is a kernel facility and is not "
                          "available here; Feature 3 does not use it.",
                "linux_reference": "https://man7.org/linux/man-pages/man8/tc-netem.8.html",
            },
            "physical_path_control": {
                "supported": False,
                "reason": "No interface, route or firewall rule is modified; no "
                          "privileged action is taken by this feature.",
            },
        },
        "timestamp": time.time(),
    }
