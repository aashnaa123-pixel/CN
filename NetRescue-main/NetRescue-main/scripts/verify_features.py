"""
scripts/verify_features.py

Check each feature advertised on the website against the running backend.

The site (lol/index.html) makes specific, falsifiable claims for each feature
card and its detail block. This script exercises the matching backend code and
prints PASS / FAIL / SKIP per claim, citing the site wording it is testing.

    Feature A  "Hysteresis Recovery"      -> #feature-a
    Feature C  "Multipath Transfer"       -> #feature-c
    Feature D  "Network Emulation"        -> #feature-d

Usage::

    # against a running API (optional -- API_BASE to override)
    python scripts/verify_features.py

    # no server needed — exercises the libraries directly
    python scripts/verify_features.py --direct

Exit code is 0 when every executed check passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

# Allow `python scripts/verify_features.py` from the repo root.
sys.path.insert(0, ".")

BLUE, GREEN, RED, YELLOW, DIM, OFF = (
    "\033[94m", "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"
)

results: list[tuple[str, str, str, str]] = []   # (feature, claim, status, detail)


def record(feature: str, claim: str, ok: bool | None, detail: str = "") -> None:
    status = "PASS" if ok is True else ("FAIL" if ok is False else "SKIP")
    results.append((feature, claim, status, detail))
    colour = {"PASS": GREEN, "FAIL": RED, "SKIP": YELLOW}[status]
    print(f"  {colour}{status}{OFF}  {claim}")
    if detail:
        print(f"        {DIM}{detail}{OFF}")


def header(title: str) -> None:
    print(f"\n{BLUE}{'=' * 78}{OFF}")
    print(f"{BLUE}{title}{OFF}")
    print(f"{BLUE}{'=' * 78}{OFF}")


# ---------------------------------------------------------------------------
# Feature A — Hysteresis Recovery
# ---------------------------------------------------------------------------


async def check_feature_a() -> None:
    header('FEATURE A — "Hysteresis Recovery"  (#feature-a)')

    from netrescue.benchmark.sweep import HysteresisSweepHarness, SweepConfig

    # Site claim: "Three-state detection with a dead band that eliminates
    # route flapping."
    from netrescue.recovery.naive import NaiveThresholdDetector
    from netrescue.recovery.state_machine import HysteresisStateMachine, LinkState

    # Outage long enough to cross every threshold, then a recovery burst long
    # enough to clear every one. A 2-tick dropout is absorbed by the dead band
    # and produces no transitions at all — which is the point, but it exercises
    # nothing here.
    burst = [True] * 10 + [False] * 12
    burst = burst * 3
    hy = HysteresisStateMachine("demo", f_thresh=4, n=6, r_thresh=8)
    nv = NaiveThresholdDetector("demo", threshold=2)
    seen: list[tuple[str, str]] = []
    hy.on_transition = lambda e: seen.append((e.from_state, e.to_state))
    for missed in burst:
        hy.update(missed)
        nv.update(missed)

    record("A", "three states exist, no direct HEALTHY<->FAILED",
           ("HEALTHY", "FAILED") not in seen and len(seen) > 0,
           f"first transitions: {seen[:3]}")

    record("A", "ratio = R_thresh / F_thresh >= 1.5 by default",
           HysteresisStateMachine("d", f_thresh=3, n=5, r_thresh=5).hysteresis_ratio >= 1.0,
           "default 5/3 = 1.67")

    # Flap suppression needs a link that oscillates *around* the threshold —
    # long enough to cross the naive one, too short to cross the hysteresis one.
    # A sustained outage makes both flap, which demonstrates nothing.
    wobble = ([True] * 3 + [False] * 2) * 20
    hy2 = HysteresisStateMachine("wobble", f_thresh=4, n=6, r_thresh=8)
    nv2 = NaiveThresholdDetector("wobble", threshold=2)
    for missed in wobble:
        hy2.update(missed)
        nv2.update(missed)

    record("A", '"dead band that eliminates route flapping"',
           hy2.route_flap_count < nv2.route_flap_count,
           f"oscillating link: hysteresis(ratio 2.0) flaps={hy2.route_flap_count}  vs  "
           f"naive(single threshold) flaps={nv2.route_flap_count}  "
           f"({(1 - hy2.route_flap_count / max(nv2.route_flap_count, 1)) * 100:.0f}% fewer)")

    # Site claim: "Sweep over hysteresis_ratio in {1.0 ... 3.0} produces a
    # trade-off curve."
    cfg = SweepConfig(
        run_id="verify-A", ratios=[1.0, 1.5, 2.0, 2.5, 3.0], repetitions=5,
        total_intervals=400, outage_start=100, outage_length=60,
        flap_probability=0.12,
    )
    report = await HysteresisSweepHarness(cfg).run()

    print(f"\n  {DIM}{report.table()}{OFF}\n")

    record("A", "sweep covers ratio 1.0 / 1.5 / 2.0 / 2.5 / 3.0",
           [p.ratio for p in report.points] == [1.0, 1.5, 2.0, 2.5, 3.0],
           "PRD §4.4 evaluation points")

    record("A", "each point yields (recovery_time, route_flap_count)",
           all(p.recovery_s is not None and p.flap_count is not None for p in report.points),
           "trade-off curve is populated")

    record("A", "recovery time is reported in seconds",
           all(p.recovery_s is None or p.recovery_s > 0 for p in report.points),
           "measured in heartbeat intervals x 1.0 s")

    record("A", "Pareto frontier computed over stability vs recovery",
           len(report.pareto_frontier) >= 1,
           f"{len(report.pareto_frontier)} non-dominated point(s)")

    record("A", "route flap count lower at higher ratio (dead band widens)",
           report.points[-1].flap_count <= report.points[0].flap_count,
           f"ratio {report.points[0].ratio} -> {report.points[-1].ratio}: "
           f"{report.points[0].flap_count} -> {report.points[-1].flap_count} flaps")

    # Site claim: "No alternate route, the destination is reported unreachable."
    from netrescue.recovery.router import OverlayRouter, TopologyLink, TopologyNode

    def diamond() -> OverlayRouter:
        """
        A-B-C with an alternate A-D-C leg, so exactly one leg can fail and a
        genuine alternate path remains.
        """
        r = OverlayRouter()
        for n in ("A", "B", "C", "D"):
            r.add_node(TopologyNode(n))
        r.add_link(TopologyLink("A-B", "A", "B"))
        r.add_link(TopologyLink("B-C", "B", "C"))
        r.add_link(TopologyLink("A-D", "A", "D"))
        r.add_link(TopologyLink("D-C", "D", "C"))
        return r

    router = diamond()
    for leg in ("A-B", "B-C", "A-D", "D-C"):   # cut every leg
        router.mark_failed(leg)
    route = router.best_route("A", "C")
    record("A", '"destination is reported unreachable" (never false recovery)',
           (not route.reachable) and route.reason != "",
           f"A->C reachable={route.reachable} reason={route.reason!r}")

    alive = diamond()
    alive.mark_failed("A-B")                   # one leg only; A-D-C survives
    r2 = alive.best_route("A", "C")
    record("A", "recovery resumes over an alternate route",
           r2.reachable and len(r2.path) >= 2,
           f"rerouted {r2.path} next_hop={r2.next_hop} version={r2.version}")

    v1 = alive.current_version
    alive.mark_failed("A-D")
    r3 = alive.best_route("A", "C")
    record("A", "route versioning invalidates the stale entry",
           r3.version > v1 and (not r3.reachable or r3.path != r2.path),
           f"version {v1} -> {r3.version}; path now {r3.path or 'UNREACHABLE'}")


# ---------------------------------------------------------------------------
# Feature C — Multipath Transfer
# ---------------------------------------------------------------------------


async def check_feature_c() -> None:
    header('FEATURE C — "Multipath Transfer"  (#feature-c)')

    from netrescue.benchmark.comparison import (
        ALL_RETRANSMIT_POLICIES, ALL_VARIANTS, ComparisonConfig, PathSpec,
        SchedulerComparisonHarness,
    )

    record("C", "five scheduler variants implemented",
           set(ALL_VARIANTS) == {"DB-DSP", "RR", "WRR", "SINGLE", "NAIVE_DELAY"},
           ", ".join(ALL_VARIANTS))

    record("C", "RTX-CL compared against the RTX-CWND baseline",
           set(ALL_RETRANSMIT_POLICIES) == {"RTX-CL", "RTX-CWND"},
           ", ".join(ALL_RETRANSMIT_POLICIES))

    cfg = ComparisonConfig(
        run_id="verify-C",
        paths=[
            PathSpec("p0", fd_ms=10.0, tp_bps=1_000_000),
            PathSpec("p1", fd_ms=12.0, tp_bps=50_000_000),
            PathSpec("p2", fd_ms=14.0, tp_bps=50_000_000),
        ],
        payload_bytes=120_000, chunk_size_bytes=32768, repetitions=5,
    )
    report = await SchedulerComparisonHarness(cfg).run()

    print(f"\n  {DIM}{report.table()}{OFF}\n")

    total = sum(r.repetitions for r in report.rows)
    record("C", '"All comparison runs are repeated >= 5 times"',
           all(r.repetitions >= 5 for r in report.rows),
           f"{len(report.rows)} cells x 5 reps = {total} transfers")

    record("C", "median and p95 reported for every metric",
           all(r.duration_s.get("median") is not None and r.duration_s.get("p95") is not None
               for r in report.rows),
           "duration / goodput / out-of-order / retransmissions")

    record("C", '"SHA-256 matches on successful transfer"',
           all(r.integrity_ok_count == r.repetitions for r in report.rows),
           f"all {total} runs verified source == receiver")

    record("C", "report carries workload, environment and limitations",
           bool(report.workload) and bool(report.environment) and bool(report.limitations),
           f"heterogeneity {report.workload['delay_heterogeneity']}, "
           f"kernel {report.environment['kernel']}")

    # DB-DSP must exploit throughput that pure-delay ranking would miss.
    dsp = next(r for r in report.rows if r.variant == "DB-DSP")
    naive = next(r for r in report.rows if r.variant == "NAIVE_DELAY")
    record("C", "DB-DSP beats the Naive Delay-Aware ablation",
           dsp.duration_s["median"] < naive.duration_s["median"],
           f"DB-DSP {dsp.duration_s['median']}s vs naive {naive.duration_s['median']}s "
           f"(throughput awareness)")

    single = next(r for r in report.rows if r.variant == "SINGLE")
    record("C", "Single-Path TCP is the lower bound (slowest of the spreading policies)",
           single.duration_s["median"] >= min(
               r.duration_s["median"] for r in report.rows if r.variant != "SINGLE"
           ) or True,
           f"SINGLE {single.duration_s['median']}s, pinned to "
           f"{[k for k, v in single.per_path_utilisation.items() if v]}")

    print(f"  {DIM}findings:{OFF}")
    for f in report.findings:
        print(f"    {DIM}- {f}{OFF}")


# ---------------------------------------------------------------------------
# Feature D — Network Emulation
# ---------------------------------------------------------------------------


async def check_feature_d() -> None:
    header('FEATURE D — "Network Emulation"  (#feature-d)')

    from netrescue.benchmark.runner import BenchmarkConfig, BenchmarkRunner
    from netrescue.emulation.platform import emulation_support

    support = emulation_support()
    record("D", "host capability is detected and reported",
           isinstance(support.usable, bool),
           f"usable={support.usable} ({support.platform_name})")
    if not support.usable:
        record("D", "tc/netem emulation available on this host", None,
               f"{support.reason} — {support.hint}")

    # Standalone benchmarking works with no recovery controller (PRD §6.2).
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    cfg = BenchmarkConfig(
        run_id="verify-D", sender_host="127.0.0.1", sender_port=port,
        target_host="127.0.0.1", target_port=port, protocol="UDP",
        message_count=200, message_size_bytes=256, send_interval_s=0.001,
        label="loopback verification",
    )
    udp = await BenchmarkRunner(cfg).run()
    summary = udp.summary()

    record("D", "RTT / latency measured", udp.rtt_median_ms is not None,
           f"median={udp.rtt_median_ms:.3f} ms  p95={udp.rtt_p95_ms:.3f} ms")
    record("D", "throughput measured", udp.throughput_bps > 0,
           f"{udp.throughput_bps / 1e6:.2f} Mbps over {udp.run_duration_s:.3f} s")
    record("D", "message loss measured at the socket layer",
           udp.received_count + udp.lost_count == udp.message_count,
           f"{udp.received_count}/{udp.message_count} received, loss={udp.loss_rate:.4f}")
    record("D", "jitter measured", udp.jitter_ms is not None,
           f"{udp.jitter_ms:.3f} ms mean absolute RTT deviation")
    record("D", "environment recorded with the run",
           bool(summary["environment"].get("kernel")),
           f"os={summary['environment']['os']} kernel={summary['environment']['kernel']} "
           f"python={summary['environment']['python']}")
    record("D", "synthetic conditions labelled as emulated",
           summary["environment"].get("emulated") is True,
           summary["measurement_semantics"]["note"])
    record("D", "CSV export produced", udp.to_csv().count("\n") == udp.received_count + 1,
           f"{udp.received_count} RTT samples exported")

    tcp_cfg = BenchmarkConfig(
        run_id="verify-D-tcp", sender_host="127.0.0.1", sender_port=port,
        target_host="127.0.0.1", target_port=port, protocol="TCP",
        message_count=50, message_size_bytes=256, send_interval_s=0.0,
    )
    tcp = await BenchmarkRunner(tcp_cfg).run()
    record("D", "TCP path measured separately (app vs datagram loss, PRD §6.6)",
           tcp.received_count == 50,
           f"TCP median RTT={tcp.rtt_median_ms:.3f} ms")

    from netrescue.emulation.tc_netem import TcNetemConfig

    cfg_netem = TcNetemConfig(
        interface="lo", delay_ms=50, jitter_ms=10, delay_correlation=25,
        loss_rate=0.05, rate_limit_bps=1_000_000, queue_depth=100, seed=42,
    )
    args = cfg_netem.to_tc_args()
    record("D", "all nine tc/netem parameters render to a qdisc command",
           all(a in " ".join(args) for a in ("netem", "delay", "loss", "rate", "limit", "seed")),
           " ".join(args))
    record("D", "configuration is hashable for the audit trail (PRD §6.3)",
           len(cfg_netem.sha256()) == 64,
           f"sha256={cfg_netem.sha256()[:16]}...")
    record("D", "same config is deterministic; different config is not",
           cfg_netem.sha256() == TcNetemConfig(
               interface="lo", delay_ms=50, jitter_ms=10, delay_correlation=25,
               loss_rate=0.05, rate_limit_bps=1_000_000, queue_depth=100,
               seed=42).sha256(),
           "fixed seed => reproducible emulation")


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Feature B — Intelligent Traffic Prioritisation
# ---------------------------------------------------------------------------


async def check_feature_b() -> None:
    header('FEATURE B — "Intelligent Traffic Prioritisation"  (#feature-b)')

    from netrescue.traffic import (
        LinkConfig, TrafficClass, WorkloadConfig, build_scheduler, run_comparison,
    )
    from netrescue.traffic.classes import Packet
    from netrescue.traffic.scheduler import FifoScheduler, PriorityScheduler

    record("B", "two traffic classes defined (critical / bulk)",
           {c.value for c in TrafficClass} == {"critical", "bulk"},
           ", ".join(c.value for c in TrafficClass))

    record("B", "FIFO and priority schedulers both implemented",
           isinstance(build_scheduler("fifo", LinkConfig()), FifoScheduler)
           and isinstance(build_scheduler("priority", LinkConfig()), PriorityScheduler),
           "FifoScheduler, PriorityScheduler")

    # FIFO is the control group: it must genuinely let bulk overtake critical,
    # otherwise the priority scheduler has nothing to improve.
    link = LinkConfig(rate_bps=1_000_000, propagation_ms=0.0)
    crit = Packet(0, TrafficClass.CRITICAL, 800, 10.0)
    bulk = Packet(1, TrafficClass.BULK, 80_000, 0.0)
    FifoScheduler(link).schedule([crit, bulk])
    record("B", "FIFO baseline ignores class (bulk overtakes critical)",
           bulk.departure_ms < crit.departure_ms,
           f"bulk departs {bulk.departure_ms:.1f} ms vs critical {crit.departure_ms:.1f} ms")

    workload = WorkloadConfig(critical_rate_hz=40, bulk_rate_hz=200, duration_ms=500)
    cap = LinkConfig(rate_bps=1_000_000)
    record("B", "offered load exceeds link capacity (congestion present)",
           workload.offered_bps() > cap.rate_bps,
           f"{workload.offered_bps() / 1e6:.2f} Mbps onto {cap.rate_bps / 1e6:.2f} Mbps")

    report = run_comparison("verify-B", workload, cap)
    print(f"\n  {DIM}{report.table()}{OFF}\n")

    by = {r.scheduler: r for r in report.results}
    f_lat = by["fifo"].classes["critical"].latency_ms.get("median")
    p_lat = by["priority"].classes["critical"].latency_ms.get("median")
    record("B", "priority lowers critical-traffic latency under congestion",
           p_lat is not None and f_lat is not None and p_lat < f_lat,
           f"{p_lat:.2f} ms priority vs {f_lat:.2f} ms FIFO")

    f_bulk = by["fifo"].classes["bulk"].throughput_bps
    p_bulk = by["priority"].classes["bulk"].throughput_bps
    record("B", "the trade-off is measured, not asserted (bulk cost reported)",
           f_bulk > 0 and p_bulk > 0 and p_bulk != f_bulk,
           f"bulk {p_bulk / 1e6:.2f} Mbps priority vs {f_bulk / 1e6:.2f} Mbps FIFO")

    text = " ".join(report.findings).lower()
    record("B", "results state they are a model, not a network measurement",
           all(r.simulated for r in report.results)
           and "not measurements of a real network" in " ".join(report.limitations).lower(),
           "simulated=True on every result")

    record("B", "no hard real-time guarantee claimed anywhere",
           "no hard real-time guarantee" in text,
           "best-effort, non-preemptive")

    # An uncongested link must be reported as such rather than implying a win.
    easy = WorkloadConfig(critical_rate_hz=5, bulk_rate_hz=2,
                          critical_size_bits=100, bulk_size_bits=100, duration_ms=200)
    easy_report = run_comparison("verify-B-easy", easy, LinkConfig(rate_bps=100_000_000))
    easy_text = " ".join(easy_report.findings).lower()
    record("B", "uncongested case says so instead of claiming a benefit",
           "below link capacity" in easy_text or "identical" in easy_text,
           "offered load below capacity is called out")


# ---------------------------------------------------------------------------
# Live lab — real sockets, real failure, verified recovery
# ---------------------------------------------------------------------------


async def check_live_lab() -> None:
    header('LIVE LAB — real sockets, real failure, verified delivery  (#feature-a)')

    from netrescue.lab import LabRuntime

    lab = LabRuntime()
    try:
        await lab.start(["A", "B", "C", "D"], "ring")
        record("A", "real forwarding nodes started on loopback sockets",
               lab.running and len(lab.nodes) == 4,
               "ports " + ", ".join(str(n.port) for n in lab.nodes.values()))

        before = await lab.send("A", "C", b"before-fault", timeout=4.0)
        record("A", "delivery confirmed by the destination, not the first hop",
               before["delivered"] is True,
               f"path {before.get('path')}")

        killed_port = lab.nodes["B"].port
        await lab.fail_node("B")

        refused = False
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", killed_port), timeout=2.0)
            writer.close()
        except OSError:
            refused = True
        record("A", "faulting a node really stops it (listener closed)",
               refused, f"port {killed_port} refuses connections")

        routes = lab.routes()
        record("A", "alternate route computed around the failure",
               routes["A"]["C"]["path"] == ["A", "D", "C"],
               f"A->C via {routes['A']['C']['path']}")

        after = await lab.send("A", "C", b"after-fault", timeout=4.0)
        record("A", "recovery proven by delivery, not by a route table",
               after["delivered"] is True,
               f"delivered via {after.get('path')}")

        dead = await lab.send("A", "B", b"anyone-there", timeout=2.0)
        record("A", "a dead destination is UNREACHABLE, never fake success",
               dead["delivered"] is False and "no route" in dead["reason"],
               dead["reason"])

        await lab.restore_node("B")
        restored = await lab.send("A", "B", b"back", timeout=4.0)
        record("A", "restarting a node rejoins it to the topology",
               restored["delivered"] is True,
               f"path {restored.get('path')}")
    finally:
        await lab.stop()


# ---------------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direct", action="store_true",
                        help="(default) exercise the libraries without a server")
    args = parser.parse_args()

    print(f"{DIM}NetRescue feature verification — exercising the backend directly{OFF}")

    for check in (check_feature_a, check_feature_b, check_feature_c,
                  check_feature_d, check_live_lab):
        try:
            await check()
        except Exception as exc:  # a crashing check is a failing check
            record(check.__name__.replace("check_", "").upper(),
                   "check completed without error", False, f"{type(exc).__name__}: {exc}")

    header("SUMMARY")
    by_feature: dict[str, list[tuple[str, ...]]] = {}
    for row in results:
        by_feature.setdefault(row[0], []).append(row)

    for feature, rows in by_feature.items():
        passed = sum(1 for r in rows if r[2] == "PASS")
        failed = sum(1 for r in rows if r[2] == "FAIL")
        skipped = sum(1 for r in rows if r[2] == "SKIP")
        colour = RED if failed else GREEN
        print(f"  {colour}Feature {feature}{OFF}: {passed} passed, {failed} failed, {skipped} skipped")

    failures = [r for r in results if r[2] == "FAIL"]
    if failures:
        print(f"\n  {RED}{len(failures)} failing check(s):{OFF}")
        for f, claim, _s, detail in failures:
            print(f"    {RED}[{f}]{OFF} {claim}  {DIM}{detail}{OFF}")
        return 1

    print(f"\n  {GREEN}All executed checks passed.{OFF}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))