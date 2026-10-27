"""
netrescue/labs/tcp_udp.py — Feature 7: TCP vs UDP performance lab.

Two clearly separated modes. Every result carries `mode`, and nothing from one
mode is ever reported as the other:

  real_loopback  Actual TCP and UDP sockets on 127.0.0.1, measured with
                 time.perf_counter(). Loopback has no physical link, so there is
                 no propagation delay, no bandwidth limit and normally no
                 network loss. These numbers describe this machine's loopback
                 stack and Python, NOT a LAN or the Internet. Controlled loss or
                 delay is NOT injected here — that would need a kernel facility
                 (tc/netem, WinDivert) this project deliberately does not use.

  simulated      TCP (the Reno model in tcp_cc.py) versus a UDP sender over the
                 same modelled bottleneck, with a configurable RTT, loss and
                 bandwidth. No sockets are involved.

Findings are generated from the numbers of the run that produced them. They
never state that one protocol is faster or better in general.
"""

from __future__ import annotations

import platform
import random
import socket
import statistics
import sys
import threading
import time
from dataclasses import dataclass

from netrescue.labs.tcp_cc import (
    BottleneckLink,
    SimulationError,
    TcpParams,
    simulate_tcp,
)

PROTOCOL_NOTES = [
    {"aspect": "Connection setup",
     "tcp": "Three-way handshake (SYN, SYN-ACK, ACK) before any data: one extra round trip.",
     "udp": "None. The first datagram carries data."},
    {"aspect": "Reliability",
     "tcp": "Lost segments are detected (duplicate ACKs, timeouts) and retransmitted until acknowledged.",
     "udp": "None. A lost datagram is gone unless the application retransmits it."},
    {"aspect": "Ordering",
     "tcp": "A byte stream: the receiver gets bytes in order; a gap holds back later data.",
     "udp": "Independent datagrams; they may arrive reordered, duplicated or not at all."},
    {"aspect": "Acknowledgements",
     "tcp": "Cumulative ACKs drive retransmission and the congestion window.",
     "udp": "None at the protocol level."},
    {"aspect": "Congestion control",
     "tcp": "Reduces its sending rate when it infers congestion.",
     "udp": "None built in; the sender sets its own rate."},
    {"aspect": "Message boundaries",
     "tcp": "None: the stream must be framed by the application.",
     "udp": "Preserved: one send = one datagram."},
]


class LabError(ValueError):
    """Invalid parameters; message is safe to show to a user."""


# ---------------------------------------------------------------------------
# Real loopback experiment
# ---------------------------------------------------------------------------

_real_lock = threading.Lock()


class LabBusy(RuntimeError):
    """A real-socket experiment is already running."""


@dataclass
class RealParams:
    ping_count: int = 200            # request/response round trips per protocol
    payload_bytes: int = 512         # size of each ping message (UDP: <= 1400)
    bulk_bytes: int = 8_000_000      # TCP bulk transfer size
    udp_datagrams: int = 5000        # UDP blast datagram count
    udp_datagram_bytes: int = 1000   # UDP blast datagram size (>= 8)

    def validate(self) -> None:
        for ok, msg in [
            (10 <= self.ping_count <= 2000, "ping_count must be between 10 and 2000"),
            (8 <= self.payload_bytes <= 1400, "payload_bytes must be between 8 and 1400"),
            (100_000 <= self.bulk_bytes <= 64_000_000,
             "bulk_bytes must be between 100000 and 64000000"),
            (100 <= self.udp_datagrams <= 50_000,
             "udp_datagrams must be between 100 and 50000"),
            (8 <= self.udp_datagram_bytes <= 1400,
             "udp_datagram_bytes must be between 8 and 1400"),
        ]:
            if not ok:
                raise LabError(msg)


def _stats(samples: list[float]) -> dict:
    if not samples:
        return {"n": 0, "mean_ms": None, "p50_ms": None, "p95_ms": None, "max_ms": None}
    s = sorted(samples)
    pick = lambda q: s[min(len(s) - 1, int(round(q * (len(s) - 1))))]
    return {"n": len(s), "mean_ms": round(statistics.fmean(s) * 1000, 4),
            "p50_ms": round(pick(0.5) * 1000, 4), "p95_ms": round(pick(0.95) * 1000, 4),
            "max_ms": round(s[-1] * 1000, 4)}


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed early")
        buf += chunk
    return bytes(buf)


def _tcp_experiment(rp: RealParams) -> dict:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(2)
    port = srv.getsockname()[1]
    errors: list[str] = []

    def serve() -> None:
        try:
            for _ in range(2):
                conn, _ = srv.accept()
                with conn:
                    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    conn.settimeout(10)
                    mode = _recv_exact(conn, 1)
                    if mode == b"E":            # echo: ping-pong
                        for _ in range(rp.ping_count):
                            conn.sendall(_recv_exact(conn, rp.payload_bytes))
                    else:                        # sink: read bulk, then 1-byte ack
                        remaining = rp.bulk_bytes
                        while remaining > 0:
                            data = conn.recv(min(262144, remaining))
                            if not data:
                                raise ConnectionError("sink closed early")
                            remaining -= len(data)
                        conn.sendall(b"K")
        except Exception as exc:                # surfaced in the result
            errors.append(f"server: {exc}")

    th = threading.Thread(target=serve, daemon=True)
    th.start()
    result: dict = {"protocol": "tcp"}
    try:
        t0 = time.perf_counter()
        c = socket.create_connection(("127.0.0.1", port), timeout=10)
        result["connect_ms"] = round((time.perf_counter() - t0) * 1000, 4)
        with c:
            c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            c.sendall(b"E")
            msg = bytes(range(256)) * (rp.payload_bytes // 256 + 1)
            msg = msg[:rp.payload_bytes]
            rtts: list[float] = []
            for _ in range(rp.ping_count):
                t = time.perf_counter()
                c.sendall(msg)
                _recv_exact(c, rp.payload_bytes)
                rtts.append(time.perf_counter() - t)
        result["latency"] = _stats(rtts)

        c = socket.create_connection(("127.0.0.1", port), timeout=10)
        with c:
            c.sendall(b"B")
            block = b"x" * 65536
            sent = 0
            t = time.perf_counter()
            while sent < rp.bulk_bytes:
                n = min(len(block), rp.bulk_bytes - sent)
                c.sendall(block[:n])
                sent += n
            _recv_exact(c, 1)
            elapsed = time.perf_counter() - t
        result["bulk"] = {
            "bytes_sent": rp.bulk_bytes, "bytes_delivered": rp.bulk_bytes,
            "delivered_ratio": 1.0, "elapsed_s": round(elapsed, 5),
            "throughput_mbps": round(rp.bulk_bytes * 8 / elapsed / 1e6, 2),
            "note": "Delivery is confirmed by the receiver's end-of-transfer acknowledgement.",
        }
    except Exception as exc:
        errors.append(f"client: {exc}")
    finally:
        th.join(timeout=5)
        srv.close()
    result["errors"] = errors
    return result


def _udp_experiment(rp: RealParams) -> dict:
    echo = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    echo.bind(("127.0.0.1", 0))
    echo.settimeout(0.2)
    sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sink.bind(("127.0.0.1", 0))
    sink.settimeout(0.5)
    echo_port, sink_port = echo.getsockname()[1], sink.getsockname()[1]
    stop = threading.Event()
    received: list[int] = []
    errors: list[str] = []

    def echo_loop() -> None:
        while not stop.is_set():
            try:
                data, addr = echo.recvfrom(2048)
                echo.sendto(data, addr)
            except socket.timeout:
                continue
            except OSError:
                return

    def sink_loop() -> None:
        started = False
        while True:
            try:
                data, _ = sink.recvfrom(2048)
                started = True
                received.append(int.from_bytes(data[:8], "big"))
            except socket.timeout:
                if started or stop.is_set():
                    return
            except OSError:
                return

    t_echo = threading.Thread(target=echo_loop, daemon=True)
    t_sink = threading.Thread(target=sink_loop, daemon=True)
    t_echo.start()
    t_sink.start()
    result: dict = {"protocol": "udp", "connect_ms": 0.0,
                    "connect_note": "UDP has no connection setup."}
    try:
        cli = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        cli.settimeout(0.5)
        with cli:
            msg = bytes(rp.payload_bytes)
            rtts: list[float] = []
            lost = 0
            for i in range(rp.ping_count):
                t = time.perf_counter()
                cli.sendto(msg, ("127.0.0.1", echo_port))
                try:
                    cli.recvfrom(2048)
                    rtts.append(time.perf_counter() - t)
                except socket.timeout:
                    lost += 1
            result["latency"] = _stats(rtts)
            result["latency"]["timeouts"] = lost

            body = bytes(rp.udp_datagram_bytes - 8)
            t = time.perf_counter()
            for i in range(rp.udp_datagrams):
                cli.sendto(i.to_bytes(8, "big") + body, ("127.0.0.1", sink_port))
            send_elapsed = time.perf_counter() - t
        t_sink.join(timeout=5)
        got = len(received)
        unique = len(set(received))
        reordered = sum(1 for a, b in zip(received, received[1:]) if b < a)
        last_rx = max(send_elapsed, 1e-9)
        result["bulk"] = {
            "datagrams_sent": rp.udp_datagrams,
            "datagrams_received": got,
            "unique_received": unique,
            "duplicates": got - unique,
            "reordered": reordered,
            "delivered_ratio": round(unique / rp.udp_datagrams, 5),
            "send_elapsed_s": round(send_elapsed, 5),
            "send_rate_mbps": round(rp.udp_datagrams * rp.udp_datagram_bytes * 8
                                    / last_rx / 1e6, 2),
            "delivered_mbps": round(unique * rp.udp_datagram_bytes * 8
                                    / last_rx / 1e6, 2),
            "note": ("The sender is not slowed by the receiver. Any shortfall in "
                     "delivered_ratio is real datagram loss (e.g. receive-buffer "
                     "overflow) observed on this machine; 1.0 means none was seen "
                     "in this run, not that UDP is lossless."),
        }
    except Exception as exc:
        errors.append(f"client: {exc}")
    finally:
        stop.set()
        t_echo.join(timeout=2)
        echo.close()
        sink.close()
    result["errors"] = errors
    return result


def run_real_loopback(rp: RealParams) -> dict:
    rp.validate()
    if not _real_lock.acquire(blocking=False):
        raise LabBusy("a real-socket experiment is already running; try again shortly")
    try:
        started = time.perf_counter()
        tcp = _tcp_experiment(rp)
        udp = _udp_experiment(rp)
        duration = time.perf_counter() - started
    finally:
        _real_lock.release()

    findings = _real_findings(tcp, udp)
    return {
        "kind": "measurement",
        "mode": "real_loopback",
        "label": "REAL — measured on this machine's loopback interface (127.0.0.1)",
        "params": rp.__dict__.copy(),
        "method": [
            "Sockets are real; both endpoints run in this process on 127.0.0.1 (threads).",
            "Latency: sequential request/response round trips, timed with perf_counter, one in flight.",
            "TCP bulk: sendall() of bulk_bytes in 64 KiB blocks, timed until the receiver's 1-byte ack.",
            "UDP bulk: datagrams sent back to back; the receiver counts what arrives (sequence numbers).",
            "TCP connect time is the time for connect() to return (kernel handshake).",
        ],
        "run_duration_s": round(duration, 3),
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "tcp": tcp,
        "udp": udp,
        "findings": findings,
        "limitations": [
            "Loopback has no propagation delay, bandwidth limit or normal packet loss; results do not predict LAN or Internet behaviour.",
            "Sender, receiver and the Python interpreter share one machine and one GIL; absolute numbers depend on load.",
            "No loss or delay is injected, so this mode cannot show TCP retransmission or UDP loss under impairment — use simulated mode for that.",
            "Single run per call; repeat to see variance.",
        ],
    }


def _real_findings(tcp: dict, udp: dict) -> list[str]:
    out: list[str] = []
    if tcp["errors"] or udp["errors"]:
        out.append("Some steps failed: " + "; ".join(tcp["errors"] + udp["errors"]))
    tl, ul = tcp.get("latency"), udp.get("latency")
    if tl and ul and tl["n"] and ul["n"]:
        out.append(f"Median round-trip on loopback: TCP {tl['p50_ms']} ms, UDP {ul['p50_ms']} ms "
                   f"(small messages, this run only).")
    if "connect_ms" in tcp:
        out.append(f"TCP connect() took {tcp['connect_ms']} ms; UDP needs no setup.")
    ub, tb = udp.get("bulk"), tcp.get("bulk")
    if ub and tb:
        out.append(f"Bulk: TCP moved {tb['throughput_mbps']} Mbit/s with 100% delivery; UDP sent at "
                   f"{ub['send_rate_mbps']} Mbit/s and {ub['delivered_ratio']*100:.2f}% of datagrams "
                   f"arrived ({ub['reordered']} reordered, {ub['duplicates']} duplicated).")
    out.append("These figures describe loopback on this machine only; they do not show that "
               "either protocol is generally faster.")
    return out


# ---------------------------------------------------------------------------
# Simulated comparison
# ---------------------------------------------------------------------------


@dataclass
class SimParams:
    rtt_ms: float = 60.0
    bandwidth_mbps: float = 10.0
    loss_rate: float = 0.01
    buffer_packets: int = 40
    transfer_bytes: int = 2_000_000
    udp_send_rate_mbps: float = 8.0
    packet_bytes: int = 1460
    duration_s: float = 60.0
    seed: int = 11


def simulate_udp(p: SimParams) -> dict:
    """UDP sender pacing packets at a fixed rate into the same bottleneck model."""
    rng = random.Random(p.seed)
    link = BottleneckLink(p.bandwidth_mbps, p.buffer_packets, p.loss_rate, rng)
    n = -(-p.transfer_bytes // p.packet_bytes)
    gap = p.packet_bytes * 8 / (p.udp_send_rate_mbps * 1e6)
    delays: list[float] = []
    last_arrival = 0.0
    for i in range(n):
        t = i * gap
        outcome, depart = link.offer(t, p.packet_bytes)
        if outcome == "ok":
            arrive = depart + p.rtt_ms / 2000.0
            delays.append(arrive - t)
            last_arrival = max(last_arrival, arrive)
    delivered = len(delays)
    span = max(last_arrival, 1e-9)
    return {
        "protocol": "udp",
        "packets_sent": n,
        "packets_delivered": delivered,
        "delivered_ratio": round(delivered / n, 5),
        "queue_drops": link.queue_drops,
        "random_drops": link.random_drops,
        "bytes_delivered": delivered * p.packet_bytes,
        "completion_time_s": round(last_arrival, 4),
        "goodput_mbps": round(delivered * p.packet_bytes * 8 / span / 1e6, 4),
        "one_way_delay_mean_ms": round(statistics.fmean(delays) * 1000, 3) if delays else None,
        "complete": delivered == n,
    }


def run_simulated(p: SimParams) -> dict:
    if not 1 <= p.udp_send_rate_mbps <= 100:
        raise LabError("udp_send_rate_mbps must be between 1 and 100")
    if not 100 <= p.packet_bytes <= 9000:
        raise LabError("packet_bytes must be between 100 and 9000")
    if not 10_000 <= p.transfer_bytes <= 50_000_000:
        raise LabError("transfer_bytes must be between 10000 and 50000000")
    try:
        tcp_raw = simulate_tcp(TcpParams(
            rtt_ms=p.rtt_ms, bandwidth_mbps=p.bandwidth_mbps, loss_rate=p.loss_rate,
            buffer_packets=p.buffer_packets, duration_s=min(p.duration_s, 120),
            mss_bytes=p.packet_bytes, transfer_bytes=p.transfer_bytes,
            include_handshake=True, seed=p.seed))
    except SimulationError as exc:
        raise LabError(str(exc)) from exc
    udp = simulate_udp(p)
    ts = tcp_raw["summary"]
    tcp = {
        "protocol": "tcp",
        "completed": ts["completed"],
        "completion_time_s": ts["completion_time_s"],
        "bytes_delivered": ts["bytes_acked"],
        "delivered_ratio": round(ts["bytes_acked"] / p.transfer_bytes, 5) if p.transfer_bytes else 0,
        "goodput_mbps": ts["goodput_mbps"],
        "retransmissions": ts["retransmissions"],
        "timeouts": ts["timeouts"],
        "queue_drops": ts["queue_drops"],
        "random_drops": ts["random_drops"],
        "rtt_mean_ms": ts["rtt_mean_ms"],
        "handshake_ms": p.rtt_ms,
    }
    return {
        "kind": "simulation",
        "mode": "simulated",
        "label": "SIMULATED — no sockets were used; TCP is the Reno model, UDP is a paced sender",
        "params": p.__dict__.copy(),
        "tcp": tcp,
        "udp": udp,
        "cwnd_series": tcp_raw["cwnd_series"],
        "findings": _sim_findings(p, tcp, udp),
        "assumptions": [
            "Both flows cross the same modelled bottleneck (fixed rate, drop-tail buffer, seeded random loss), evaluated one at a time (they do not compete).",
            "TCP: Reno model from tcp_cc.py including a loss-free 1-RTT handshake; the transfer ends when every byte is acknowledged.",
            "UDP: packets are paced at udp_send_rate_mbps, never retransmitted; the 'transfer' ends when the last surviving packet arrives.",
            "One-way delay = queueing + serialisation + RTT/2.",
        ],
        "limitations": [
            "A model with the stated assumptions; real stacks differ (delayed ACKs, SACK, CUBIC/BBR, pacing, offloads).",
            "Results depend on the chosen UDP send rate; a different rate changes UDP's outcome.",
        ],
    }


def _sim_findings(p: SimParams, tcp: dict, udp: dict) -> list[str]:
    out: list[str] = []
    out.append(f"UDP delivered {udp['delivered_ratio']*100:.2f}% of its datagrams "
               f"({udp['packets_delivered']}/{udp['packets_sent']}); no retransmission exists to "
               f"recover the rest." if not udp["complete"] else
               "UDP delivered every datagram in this scenario (no loss occurred).")
    if tcp["completed"]:
        out.append(f"TCP delivered all {p.transfer_bytes} bytes in {tcp['completion_time_s']} s with "
                   f"{tcp['retransmissions']} retransmissions ({tcp['timeouts']} timeouts).")
        if udp["complete"] or udp["delivered_ratio"] < 1:
            faster = "UDP finished sending sooner" if udp["completion_time_s"] < tcp["completion_time_s"] \
                else "TCP finished sooner"
            out.append(f"{faster} in this scenario (UDP {udp['completion_time_s']} s vs TCP "
                       f"{tcp['completion_time_s']} s) — but UDP's figure excludes the data it lost.")
    else:
        out.append(f"TCP did not finish within the simulated time limit "
                   f"({tcp['delivered_ratio']*100:.1f}% acknowledged).")
    out.append("Change RTT, loss or the UDP send rate and the comparison can change; "
               "no general winner is implied.")
    return out
