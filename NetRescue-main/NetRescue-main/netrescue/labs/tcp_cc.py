"""
netrescue/labs/tcp_cc.py — Feature 5: TCP congestion-control simulator.

A packet-level discrete-event simulation of ONE bulk TCP flow over ONE
bottleneck link. It is a model, not a kernel experiment: no real packet is sent
and no real TCP stack is involved.

Variant simulated: TCP Reno (RFC 5681 congestion control, with the RFC 6298
retransmission timer). Only Reno is implemented; no behaviour of CUBIC, BBR,
NewReno or SACK is mixed in. Concretely:

  * Slow start: cwnd += 1 segment per new ACK while cwnd < ssthresh.
  * Congestion avoidance: cwnd += 1/cwnd per new ACK (≈ +1 segment per RTT).
  * Fast retransmit: on the 3rd duplicate ACK, retransmit the missing segment,
    ssthresh = max(flight/2, 2), cwnd = ssthresh + 3.
  * Fast recovery: each further duplicate ACK inflates cwnd by 1. The first ACK
    that advances snd_una — including a PARTIAL ACK — deflates cwnd to ssthresh
    and exits recovery (that is Reno; NewReno would stay in recovery). With
    several losses in one window this makes Reno fall back to the RTO, which is
    real Reno behaviour and is visible in the output.
  * Timeout: ssthresh = max(flight/2, 2), cwnd = 1, go-back to snd_una, RTO
    doubles (Karn: no RTT samples from retransmitted segments).

Model assumptions (also returned with every result):
  * One flow, one direction of bulk data; the receiver ACKs every segment
    immediately (no delayed ACKs) with cumulative ACKs and buffers out-of-order
    segments. The receiver window is unlimited.
  * The forward bottleneck is a FIFO drop-tail queue with a fixed rate. Packets
    can also be lost at random (independent Bernoulli loss, seeded) on the wire.
  * `rtt_ms` is the base round-trip PROPAGATION delay (split equally between
    directions); queueing and serialisation delay are added on top. ACKs are
    tiny: they are never queued, delayed or lost.
  * Segments are `mss_bytes` of payload; headers are ignored. Goodput counts
    bytes cumulatively acknowledged.
  * The three-way handshake is optional and, when included, is assumed loss-free
    and costs exactly one RTT before data flows.
"""

from __future__ import annotations

import heapq
import random
import statistics
from collections import deque
from dataclasses import dataclass
from typing import Optional

SUPPORTED_VARIANTS = ("reno",)
MAX_EVENTS = 1_500_000
MAX_SERIES_POINTS = 1500

ASSUMPTIONS = [
    "Variant: TCP Reno (RFC 5681) with the RFC 6298 retransmission timer; no other variant's behaviour is mixed in.",
    "One bulk flow, one bottleneck: FIFO drop-tail queue with a fixed rate; seeded independent random loss on the wire.",
    "rtt_ms is base propagation round-trip time; queueing and serialisation delay are added on top.",
    "ACKs are immediate and cumulative (no delayed ACK), and are never queued, delayed or lost.",
    "Receiver window is unlimited; no SACK, no timestamps, no ECN; headers are ignored.",
    "This is a simulation. It is not a measurement of any real network or TCP implementation.",
]


class SimulationError(ValueError):
    """Invalid simulation parameters. Message is safe to show to a user."""


# ---------------------------------------------------------------------------
# Bottleneck link (shared with the TCP/UDP lab)
# ---------------------------------------------------------------------------


class BottleneckLink:
    """
    Fixed-rate FIFO link with a drop-tail buffer and optional random loss.

    `occupancy` counts packets that have been accepted and not yet finished
    serialising (queued + the one in service). A packet arriving when that equals
    `buffer_packets` is dropped (tail drop).
    """

    def __init__(self, bandwidth_mbps: float, buffer_packets: int,
                 loss_rate: float, rng: random.Random) -> None:
        self.rate_bps = bandwidth_mbps * 1_000_000.0
        self.buffer = buffer_packets
        self.loss = loss_rate
        self.rng = rng
        self._free_at = 0.0
        self._departures: deque[float] = deque()
        self.queue_drops = 0
        self.random_drops = 0

    def occupancy(self, now: float) -> int:
        while self._departures and self._departures[0] <= now:
            self._departures.popleft()
        return len(self._departures)

    def offer(self, now: float, size_bytes: int) -> tuple[str, float]:
        """Offer a packet. Returns (outcome, depart_time) with outcome in
        'ok' | 'queue_drop' | 'random_drop'. A randomly lost packet still uses
        link capacity (it is lost after serialisation)."""
        if self.occupancy(now) >= self.buffer:
            self.queue_drops += 1
            return "queue_drop", now
        start = max(now, self._free_at)
        depart = start + size_bytes * 8.0 / self.rate_bps
        self._free_at = depart
        self._departures.append(depart)
        if self.loss > 0 and self.rng.random() < self.loss:
            self.random_drops += 1
            return "random_drop", depart
        return "ok", depart


# ---------------------------------------------------------------------------
# Parameters / results
# ---------------------------------------------------------------------------


@dataclass
class TcpParams:
    rtt_ms: float = 80.0
    bandwidth_mbps: float = 10.0
    loss_rate: float = 0.0
    buffer_packets: int = 40
    duration_s: float = 20.0
    mss_bytes: int = 1460
    initial_cwnd: int = 1
    initial_ssthresh: int = 64
    min_rto_ms: float = 200.0
    transfer_bytes: Optional[int] = None   # stop when this many bytes are ACKed
    include_handshake: bool = False
    seed: int = 1
    variant: str = "reno"

    def validate(self) -> None:
        if self.variant not in SUPPORTED_VARIANTS:
            raise SimulationError(f"variant must be one of {list(SUPPORTED_VARIANTS)}")
        checks = [
            (0.1 <= self.rtt_ms <= 2000, "rtt_ms must be between 0.1 and 2000"),
            (0.1 <= self.bandwidth_mbps <= 100, "bandwidth_mbps must be between 0.1 and 100"),
            (0.0 <= self.loss_rate < 0.5, "loss_rate must be in [0, 0.5)"),
            (1 <= self.buffer_packets <= 5000, "buffer_packets must be between 1 and 5000"),
            (0.5 <= self.duration_s <= 120, "duration_s must be between 0.5 and 120"),
            (100 <= self.mss_bytes <= 9000, "mss_bytes must be between 100 and 9000"),
            (1 <= self.initial_cwnd <= 100, "initial_cwnd must be between 1 and 100"),
            (2 <= self.initial_ssthresh <= 10000, "initial_ssthresh must be between 2 and 10000"),
            (1 <= self.min_rto_ms <= 5000, "min_rto_ms must be between 1 and 5000"),
            (self.transfer_bytes is None or self.transfer_bytes >= self.mss_bytes,
             "transfer_bytes must be at least one MSS"),
        ]
        for ok, msg in checks:
            if not ok:
                raise SimulationError(msg)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def _downsample(points: list, limit: int = MAX_SERIES_POINTS) -> list:
    if len(points) <= limit:
        return points
    stride = len(points) / limit
    out = [points[int(i * stride)] for i in range(limit)]
    out[-1] = points[-1]
    return out


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

_DATA_ARRIVE, _ACK_ARRIVE, _RTO = 0, 1, 2


def simulate_tcp(params: TcpParams) -> dict:
    params.validate()
    p = params
    rng = random.Random(p.seed)
    link = BottleneckLink(p.bandwidth_mbps, p.buffer_packets, p.loss_rate, rng)
    half = p.rtt_ms / 2000.0
    total_segments = (None if p.transfer_bytes is None
                      else -(-p.transfer_bytes // p.mss_bytes))

    # Sender state -----------------------------------------------------
    cwnd = float(p.initial_cwnd)
    ssthresh = float(p.initial_ssthresh)
    snd_una = 0
    snd_nxt = 0
    high_sent = 0            # one past the highest segment ever transmitted
    dupacks = 0
    in_recovery = False
    srtt: Optional[float] = None
    rttvar = 0.0
    rto = max(1.0, p.min_rto_ms / 1000.0)
    timer_gen = 0
    send_time: dict[int, float] = {}     # seq -> first-send time (Karn: cleared on rexmit)
    # Receiver state ---------------------------------------------------
    rcv_next = 0
    rcv_buffer: set[int] = set()

    events: list[tuple[float, int, int, int]] = []   # (time, tiebreak, kind, value)
    counter = 0

    def push(t: float, kind: int, value: int) -> None:
        nonlocal counter
        counter += 1
        heapq.heappush(events, (t, counter, kind, value))

    # Instrumentation --------------------------------------------------
    cwnd_series: list[list] = []
    event_log: list[dict] = []
    acked_log: list[tuple[float, int]] = []
    rtt_samples: list[float] = []
    stats = {"fast_retransmits": 0, "timeouts": 0, "retransmissions": 0,
             "segments_sent": 0}
    completion_time: Optional[float] = None
    max_cwnd = cwnd
    processed = 0
    truncated = False

    def phase() -> str:
        if in_recovery:
            return "fast_recovery"
        return "slow_start" if cwnd < ssthresh else "congestion_avoidance"

    def record(t: float) -> None:
        nonlocal max_cwnd
        max_cwnd = max(max_cwnd, cwnd)
        cwnd_series.append([round(t, 6), round(cwnd, 3), round(ssthresh, 3),
                            phase(), high_sent - snd_una if high_sent > snd_una else 0])

    def log_event(t: float, kind: str, **extra) -> None:
        if len(event_log) < 800:
            event_log.append({"t": round(t, 6), "type": kind, **extra})

    def arm_timer(t: float) -> None:
        nonlocal timer_gen
        timer_gen += 1
        push(t + rto, _RTO, timer_gen)

    def transmit(t: float, seq: int) -> None:
        nonlocal high_sent
        retrans = seq < high_sent
        stats["segments_sent"] += 1
        if retrans:
            stats["retransmissions"] += 1
            send_time.pop(seq, None)
        else:
            send_time[seq] = t
            high_sent = seq + 1
        outcome, depart = link.offer(t, p.mss_bytes)
        if outcome == "ok":
            push(depart + half, _DATA_ARRIVE, seq)
        else:
            log_event(t, outcome, seq=seq)

    def try_send(t: float) -> None:
        nonlocal snd_nxt
        was_idle = snd_una == snd_nxt
        while snd_nxt - snd_una < int(cwnd) and (
                total_segments is None or snd_nxt < total_segments):
            transmit(t, snd_nxt)
            snd_nxt += 1
        if was_idle and snd_nxt > snd_una:
            arm_timer(t)

    start = (p.rtt_ms / 1000.0) if p.include_handshake else 0.0
    if p.include_handshake:
        log_event(0.0, "handshake", note="SYN / SYN-ACK / ACK assumed loss-free; costs 1 RTT")
    record(start)
    try_send(start)

    while events:
        t, _, kind, val = heapq.heappop(events)
        if t > p.duration_s:
            break
        processed += 1
        if processed > MAX_EVENTS:
            truncated = True
            break

        if kind == _DATA_ARRIVE:
            seq = val
            if seq >= rcv_next:
                rcv_buffer.add(seq)
                while rcv_next in rcv_buffer:
                    rcv_buffer.discard(rcv_next)
                    rcv_next += 1
            push(t + half, _ACK_ARRIVE, rcv_next)

        elif kind == _ACK_ARRIVE:
            ack = val
            if ack > snd_una:                                   # new data ACKed
                newly = ack - snd_una
                sent_at = send_time.pop(ack - 1, None)
                for s in range(snd_una, ack - 1):
                    send_time.pop(s, None)
                if sent_at is not None:                         # Karn's rule
                    sample = t - sent_at
                    rtt_samples.append(sample)
                    if srtt is None:
                        srtt, rttvar = sample, sample / 2
                    else:
                        rttvar = 0.75 * rttvar + 0.25 * abs(srtt - sample)
                        srtt = 0.875 * srtt + 0.125 * sample
                    rto = max(p.min_rto_ms / 1000.0, srtt + 4 * rttvar)
                snd_una = ack
                if snd_nxt < snd_una:
                    snd_nxt = snd_una
                dupacks = 0
                if in_recovery:                                 # Reno: any new ACK exits
                    cwnd = ssthresh
                    in_recovery = False
                    log_event(t, "exit_fast_recovery", cwnd=round(cwnd, 2))
                else:
                    for _ in range(newly):
                        cwnd += 1.0 if cwnd < ssthresh else 1.0 / cwnd
                acked_log.append((t, newly))
                if total_segments is not None and snd_una >= total_segments:
                    completion_time = t
                    record(t)
                    break
                if snd_una == snd_nxt:
                    timer_gen += 1                              # nothing outstanding
                else:
                    arm_timer(t)
                record(t)
                try_send(t)
            elif ack == snd_una and snd_nxt > snd_una:           # duplicate ACK
                dupacks += 1
                if in_recovery:
                    cwnd += 1.0
                    record(t)
                    try_send(t)
                elif dupacks == 3:
                    flight = snd_nxt - snd_una
                    ssthresh = max(flight / 2.0, 2.0)
                    cwnd = ssthresh + 3.0
                    in_recovery = True
                    stats["fast_retransmits"] += 1
                    log_event(t, "fast_retransmit", seq=snd_una,
                              ssthresh=round(ssthresh, 2))
                    transmit(t, snd_una)
                    arm_timer(t)
                    record(t)
                    try_send(t)

        else:  # _RTO
            if val != timer_gen or snd_una == snd_nxt:
                continue                                        # stale timer
            flight = snd_nxt - snd_una
            ssthresh = max(flight / 2.0, 2.0)
            cwnd = 1.0
            in_recovery = False
            dupacks = 0
            snd_nxt = snd_una                                   # go back to snd_una
            rto = min(rto * 2, 60.0)
            stats["timeouts"] += 1
            log_event(t, "timeout", seq=snd_una, ssthresh=round(ssthresh, 2),
                      rto_s=round(rto, 3))
            record(t)
            try_send(t)

    # Throughput over fixed bins ------------------------------------------
    end_t = completion_time if completion_time is not None else p.duration_s
    bin_s = max(p.rtt_ms / 1000.0, 0.1)
    n_bins = max(1, int(end_t / bin_s) + 1)
    bins = [0] * n_bins
    for t, newly in acked_log:
        bins[min(n_bins - 1, int(t / bin_s))] += newly * p.mss_bytes
    throughput = [[round(i * bin_s, 4), round(b * 8 / bin_s / 1e6, 4)]
                  for i, b in enumerate(bins)]

    delivered_bytes = snd_una * p.mss_bytes
    if p.transfer_bytes is not None:
        delivered_bytes = min(delivered_bytes, p.transfer_bytes)
    active_s = max(end_t - start, 1e-9)
    capacity_segments = p.bandwidth_mbps * 1e6 * active_s / 8 / p.mss_bytes
    summary = {
        "variant": p.variant,
        "duration_simulated_s": round(end_t, 4),
        "completed": completion_time is not None if total_segments is not None else None,
        "completion_time_s": None if completion_time is None else round(completion_time, 4),
        "bytes_acked": delivered_bytes,
        "goodput_mbps": round(delivered_bytes * 8 / active_s / 1e6, 4),
        "link_utilisation": round(min(1.0, snd_una / capacity_segments), 4) if capacity_segments else 0.0,
        "segments_sent": stats["segments_sent"],
        "retransmissions": stats["retransmissions"],
        "fast_retransmits": stats["fast_retransmits"],
        "timeouts": stats["timeouts"],
        "queue_drops": link.queue_drops,
        "random_drops": link.random_drops,
        "max_cwnd_segments": round(max_cwnd, 2),
        "final_cwnd_segments": round(cwnd, 2),
        "final_ssthresh_segments": round(ssthresh, 2),
        "rtt_mean_ms": round(statistics.fmean(rtt_samples) * 1000, 3) if rtt_samples else None,
        "rtt_p95_ms": round(_percentile(rtt_samples, 0.95) * 1000, 3) if rtt_samples else None,
        "events_processed": processed,
        "truncated": truncated,
    }
    return {
        "kind": "simulation",
        "model": "TCP Reno, single flow, single bottleneck (discrete-event)",
        "params": {k: getattr(p, k) for k in p.__dataclass_fields__},
        "summary": summary,
        "cwnd_series": _downsample(cwnd_series),
        "throughput_series": _downsample(throughput),
        "events": event_log,
        "assumptions": ASSUMPTIONS,
    }


def compare_scenarios(scenarios: list[tuple[str, TcpParams]]) -> dict:
    """Run several scenarios and tabulate the headline numbers side by side."""
    if not 1 <= len(scenarios) <= 6:
        raise SimulationError("provide between 1 and 6 scenarios")
    runs = [{"label": label, **simulate_tcp(p)} for label, p in scenarios]
    table = [{"label": r["label"], "rtt_ms": r["params"]["rtt_ms"],
              "loss_rate": r["params"]["loss_rate"],
              "bandwidth_mbps": r["params"]["bandwidth_mbps"],
              "goodput_mbps": r["summary"]["goodput_mbps"],
              "link_utilisation": r["summary"]["link_utilisation"],
              "retransmissions": r["summary"]["retransmissions"],
              "timeouts": r["summary"]["timeouts"],
              "max_cwnd_segments": r["summary"]["max_cwnd_segments"]} for r in runs]
    best = max(table, key=lambda r: r["goodput_mbps"])
    return {
        "runs": runs, "table": table,
        "finding": (f"Highest goodput in this comparison: {best['label']} "
                    f"({best['goodput_mbps']} Mbit/s). This ranks only these "
                    f"scenarios under the Reno model above."),
        "assumptions": ASSUMPTIONS,
    }
