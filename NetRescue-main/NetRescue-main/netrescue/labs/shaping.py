"""
netrescue/labs/shaping.py — Feature 6: QoS and traffic-shaping lab.

Token-bucket and leaky-bucket SHAPERS (each with a finite queue) driven by the
same packet arrival list, so any difference between the results comes from the
algorithm and not from the workload.

Assumptions (returned with every result):
  * Packets have one fixed size (`packet_bytes`); the bucket is metered in bytes.
  * Token bucket: tokens accrue continuously at `rate_bps/8` bytes per second up
    to `bucket_bytes`; the bucket starts FULL. A packet leaves as soon as the
    bucket holds at least its size, and leaves INSTANTLY (the output link is
    treated as infinitely fast), so a full bucket lets a burst of up to
    `bucket_bytes` through at once. Packets that cannot leave wait in a FIFO.
  * Leaky bucket (as a queue): packets leave strictly one after another at
    exactly `rate_bps`; each takes packet_bytes*8/rate_bps seconds. It never
    emits a burst.
  * "Buffered" means arrived and not yet departed, for both algorithms; an
    arrival that finds `queue_packets` buffered is dropped (tail drop).
  * Throughput is delivered bits over max(duration, last departure).
  * Arrival workloads are generated from a seed, so a scenario is reproducible.
    This is a model of the algorithms, not a measurement of a router.
"""

from __future__ import annotations

import bisect
import math
import random
from dataclasses import dataclass
from typing import Optional

MAX_PACKETS = 20_000
MAX_SERIES_POINTS = 1500
PATTERNS = ("steady", "burst", "overload", "poisson")

ASSUMPTIONS = [
    "Fixed packet size; the bucket is metered in bytes; the token bucket starts full.",
    "Token bucket releases a packet instantly once enough tokens exist (infinitely fast output link), so it can pass bursts up to the bucket depth.",
    "Leaky bucket releases packets one at a time at exactly the configured rate and never bursts.",
    "Both use a tail-drop FIFO; 'buffered' = arrived and not yet departed; the same queue limit applies to both.",
    "Throughput = delivered bits / max(duration, last departure). Delay = departure − arrival.",
    "Workloads are seeded and reproducible. This models the algorithms; it does not measure a router.",
]


class ShapingError(ValueError):
    """Invalid parameters; message is safe to show to a user."""


@dataclass
class ShapingParams:
    rate_bps: float = 1_000_000.0       # token rate / leak rate
    bucket_bytes: int = 6_000           # token-bucket depth (ignored by leaky bucket)
    queue_packets: int = 20
    packet_bytes: int = 1000
    duration_s: float = 5.0
    pattern: str = "burst"
    arrival_rate_bps: float = 800_000.0   # mean offered load (steady/poisson/overload)
    burst_packets: int = 15               # packets per burst (pattern=burst)
    burst_interval_s: float = 1.0         # time between burst starts
    burst_peak_bps: float = 8_000_000.0   # rate inside a burst
    seed: int = 7

    def validate(self) -> None:
        if self.pattern not in PATTERNS:
            raise ShapingError(f"pattern must be one of {list(PATTERNS)}")
        for ok, msg in [
            (1_000 <= self.rate_bps <= 1e9, "rate_bps must be between 1000 and 1e9"),
            (1 <= self.packet_bytes <= 9000, "packet_bytes must be between 1 and 9000"),
            (self.bucket_bytes >= self.packet_bytes,
             "bucket_bytes must be at least one packet, or no packet could ever be sent"),
            (self.bucket_bytes <= 10_000_000, "bucket_bytes is too large"),
            (1 <= self.queue_packets <= 5000, "queue_packets must be between 1 and 5000"),
            (0.1 <= self.duration_s <= 120, "duration_s must be between 0.1 and 120"),
            (1_000 <= self.arrival_rate_bps <= 1e9, "arrival_rate_bps must be between 1000 and 1e9"),
            (1 <= self.burst_packets <= 5000, "burst_packets must be between 1 and 5000"),
            (0.01 <= self.burst_interval_s <= 120, "burst_interval_s must be between 0.01 and 120"),
            (1_000 <= self.burst_peak_bps <= 1e9, "burst_peak_bps must be between 1000 and 1e9"),
        ]:
            if not ok:
                raise ShapingError(msg)


def generate_arrivals(p: ShapingParams) -> list[float]:
    """Arrival times (seconds) for the chosen pattern, sorted, deterministic."""
    gap = lambda bps: p.packet_bytes * 8.0 / bps
    # Reject oversized workloads BEFORE building the list, so a huge request
    # cannot exhaust memory.
    if p.pattern == "burst":
        estimate = math.ceil(p.duration_s / p.burst_interval_s) * p.burst_packets
    elif p.pattern == "overload":
        estimate = p.duration_s / gap(max(p.arrival_rate_bps, 2.0 * p.rate_bps))
    else:
        estimate = p.duration_s / gap(p.arrival_rate_bps)
    if estimate > MAX_PACKETS * 1.05:
        raise ShapingError(f"workload would have about {int(estimate)} packets; the limit is "
                           f"{MAX_PACKETS}. Lower the rate or the duration.")
    times: list[float] = []
    if p.pattern == "steady":
        g = gap(p.arrival_rate_bps)
        times = [i * g for i in range(int(p.duration_s / g) + 1)]
    elif p.pattern == "overload":
        g = gap(max(p.arrival_rate_bps, 2.0 * p.rate_bps))
        times = [i * g for i in range(int(p.duration_s / g) + 1)]
    elif p.pattern == "burst":
        g = gap(p.burst_peak_bps)
        start = 0.0
        while start < p.duration_s:
            times.extend(start + i * g for i in range(p.burst_packets))
            start += p.burst_interval_s
    else:  # poisson
        rng = random.Random(p.seed)
        mean_gap = gap(p.arrival_rate_bps)
        t = rng.expovariate(1.0 / mean_gap)
        while t < p.duration_s:
            times.append(t)
            t += rng.expovariate(1.0 / mean_gap)
    times = [t for t in times if t < p.duration_s]
    if len(times) > MAX_PACKETS:
        raise ShapingError(f"workload has {len(times)} packets; the limit is {MAX_PACKETS}. "
                           f"Lower the rate or the duration.")
    if not times:
        raise ShapingError("workload produced no packets; raise the rate or duration")
    return times


def _downsample(points: list, limit: int = MAX_SERIES_POINTS) -> list:
    if len(points) <= limit:
        return points
    stride = len(points) / limit
    out = [points[int(i * stride)] for i in range(limit)]
    out[-1] = points[-1]
    return out


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def _buffered(deps: list[float], t: float) -> int:
    """Accepted packets whose departure is after t (deps is non-decreasing)."""
    return len(deps) - bisect.bisect_right(deps, t)


def _run(p: ShapingParams, arrivals: list[float], algorithm: str) -> dict:
    byte_rate = p.rate_bps / 8.0
    deps: list[float] = []
    accepted: list[tuple[float, float]] = []   # (arrival, departure)
    dropped: list[float] = []
    token_series: list[list[float]] = []
    tokens = float(p.bucket_bytes)
    t_tokens = 0.0
    prev_dep = 0.0
    service = p.packet_bytes / byte_rate

    if algorithm == "token_bucket":
        token_series.append([0.0, round(tokens, 2)])

    for a in arrivals:
        if _buffered(deps, a) >= p.queue_packets:
            dropped.append(a)
            continue
        if algorithm == "leaky_bucket":
            dep = max(a, prev_dep) + service
        else:
            start = max(a, prev_dep)
            tokens = min(float(p.bucket_bytes), tokens + (start - t_tokens) * byte_rate)
            before = tokens
            if tokens >= p.packet_bytes - 1e-9:
                dep = start
                tokens -= p.packet_bytes
            else:
                dep = start + (p.packet_bytes - tokens) / byte_rate
                tokens = 0.0
            t_tokens = dep
            token_series.append([round(start, 6), round(before, 2)])
            token_series.append([round(dep, 6), round(tokens, 2)])
        prev_dep = dep
        deps.append(dep)
        accepted.append((a, dep))

    delays = [d - a for a, d in accepted]
    last_dep = deps[-1] if deps else 0.0
    window = max(p.duration_s, last_dep)
    delivered_bits = len(accepted) * p.packet_bytes * 8

    # Queue occupancy as a step function (+1 at accepted arrival, -1 at departure).
    steps = sorted([(a, 1) for a, _ in accepted] + [(d, -1) for _, d in accepted],
                   key=lambda e: (e[0], e[1]))
    occ, area, last_t, peak = 0, 0.0, 0.0, 0
    queue_series: list[list[float]] = [[0.0, 0]]
    for t, delta in steps:
        area += occ * (t - last_t)
        last_t = t
        occ += delta
        peak = max(peak, occ)
        queue_series.append([round(t, 6), occ])

    # Peak output rate in any 100 ms window: how bursty the output is.
    win = 0.1
    peak_bytes, j = 0, 0
    for i in range(len(deps)):
        while deps[i] - deps[j] >= win:
            j += 1
        peak_bytes = max(peak_bytes, (i - j + 1) * p.packet_bytes)

    return {
        "algorithm": algorithm,
        "summary": {
            "offered_packets": len(arrivals),
            "delivered_packets": len(accepted),
            "dropped_packets": len(dropped),
            "drop_rate": round(len(dropped) / len(arrivals), 4),
            "throughput_bps": round(delivered_bits / window, 2),
            "mean_delay_ms": round(1000 * sum(delays) / len(delays), 3) if delays else 0.0,
            "p95_delay_ms": round(1000 * _percentile(delays, 0.95), 3),
            "max_delay_ms": round(1000 * max(delays), 3) if delays else 0.0,
            "peak_queue_packets": peak,
            "mean_queue_packets": round(area / last_t, 3) if last_t > 0 else 0.0,
            "last_departure_s": round(last_dep, 4),
            "peak_output_bps_100ms": round(peak_bytes * 8 / win, 1),
        },
        "departures": _downsample([[round(a, 6), round(d, 6)] for a, d in accepted]),
        "drops": _downsample([round(t, 6) for t in dropped]),
        "queue_series": _downsample(queue_series),
        "token_series": _downsample(token_series) if algorithm == "token_bucket" else [],
    }


def compare_shapers(p: ShapingParams) -> dict:
    p.validate()
    arrivals = generate_arrivals(p)
    tb = _run(p, arrivals, "token_bucket")
    lb = _run(p, arrivals, "leaky_bucket")
    offered_bps = len(arrivals) * p.packet_bytes * 8 / p.duration_s
    findings = _findings(p, tb["summary"], lb["summary"], offered_bps)
    return {
        "kind": "simulation",
        "params": {k: getattr(p, k) for k in p.__dataclass_fields__},
        "workload": {
            "pattern": p.pattern, "packets": len(arrivals),
            "offered_bps": round(offered_bps, 1),
            "shaping_rate_bps": p.rate_bps,
            "overloaded": offered_bps > p.rate_bps,
            "arrivals": _downsample([round(t, 6) for t in arrivals]),
        },
        "token_bucket": tb,
        "leaky_bucket": lb,
        "findings": findings,
        "assumptions": ASSUMPTIONS,
    }


def _findings(p: ShapingParams, tb: dict, lb: dict, offered_bps: float) -> list[str]:
    out: list[str] = []
    if offered_bps <= p.rate_bps and tb["dropped_packets"] == 0 and lb["dropped_packets"] == 0:
        out.append("Average offered load is below the shaping rate and nothing was dropped.")
    if tb["peak_output_bps_100ms"] > lb["peak_output_bps_100ms"] * 1.05:
        out.append(
            f"Token bucket passed bursts (peak {tb['peak_output_bps_100ms']/1e6:.2f} Mbit/s "
            f"over 100 ms) that the leaky bucket smoothed to "
            f"{lb['peak_output_bps_100ms']/1e6:.2f} Mbit/s.")
    if tb["mean_delay_ms"] < lb["mean_delay_ms"]:
        out.append(f"Token bucket had lower mean delay ({tb['mean_delay_ms']} ms vs "
                   f"{lb['mean_delay_ms']} ms) because stored tokens let packets leave immediately.")
    elif tb["mean_delay_ms"] > lb["mean_delay_ms"]:
        out.append(f"Token bucket had higher mean delay ({tb['mean_delay_ms']} ms vs "
                   f"{lb['mean_delay_ms']} ms) in this scenario.")
    if tb["dropped_packets"] != lb["dropped_packets"]:
        out.append(f"Drops differ: token bucket {tb['dropped_packets']}, leaky bucket "
                   f"{lb['dropped_packets']} (same queue limit).")
    if offered_bps > p.rate_bps:
        out.append("Sustained overload: both are limited to the configured rate in the long "
                   "run; the bucket depth only changes how the first burst is handled.")
    if not out:
        out.append("The two algorithms behaved almost identically for this workload.")
    return out
