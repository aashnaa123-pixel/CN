"""
netrescue.diagnostics — Feature 3: Network Diagnostics & Benchmarking Lab.

Replaces the Linux-only `tc`/`netem` emulation capability with operations that
run on Windows 10/11 unprivileged, plus a deterministic simulator for the fault
behaviour that used to require a kernel qdisc.

    probes   real OS commands: ping, tracert, DNS, adapters, IP configuration
    runner   repeatable real benchmarks with a hashed configuration snapshot
    faults   seeded simulated fault scenarios (unreachable, loss, jitter, ...)
    service  the composed "Run Diagnostics" bundle
    harness  hysteresis sweep, scheduler comparison, Feature A/B integration
    report   exports, comparison and the measurement report
    store    bounded in-process history + best-effort persistence

Three categories are kept strictly apart and labelled everywhere:

    real        measured on this machine's network just now
    controlled  a benchmark or sweep over modelled conditions
    unsupported a capability this host cannot provide, with the reason
"""

from netrescue.diagnostics.config import SOFTWARE_VERSION, canonical_json, config_hash, normalise
from netrescue.diagnostics.faults import (
    SCENARIO_NAMES,
    FaultScenario,
    FaultScenarioResult,
    ProbeEvent,
    run_fault_scenario,
)
from netrescue.diagnostics.harness import (
    capture_baseline_measurement,
    compare_baseline,
    feature_b_side_by_side,
    hysteresis_sweep,
    scheduler_comparison,
)
from netrescue.diagnostics.probes import (
    NETWORK_POLICY_NOTES,
    ProbeError,
    ProbeResponse,
    dns_lookup,
    ip_configuration,
    is_windows,
    list_adapters,
    network_status,
    parse_ping_output,
    parse_tracert_output,
    ping,
    probe_capabilities,
    suggested_target,
    traceroute,
    validate_target,
)
from netrescue.diagnostics.report import (
    compare_runs,
    measurement_report,
    run_data_kind,
    run_to_csv,
    run_to_json,
    runs_to_csv,
)
from netrescue.diagnostics.runner import (
    BenchmarkEngine,
    BenchmarkRun,
    BenchmarkSpec,
    RepetitionOutcome,
    run_benchmark,
    simulated_benchmark,
)
from netrescue.diagnostics.service import run_full_diagnostics
from netrescue.diagnostics.store import DiagnosticsStore, diagnostics_store, persist_run

__all__ = [
    # config
    "SOFTWARE_VERSION", "canonical_json", "config_hash", "normalise",
    # probes
    "NETWORK_POLICY_NOTES", "ProbeError", "ProbeResponse", "dns_lookup",
    "ip_configuration", "is_windows", "list_adapters", "network_status",
    "parse_ping_output", "parse_tracert_output", "ping", "probe_capabilities",
    "suggested_target", "traceroute", "validate_target",
    # runner
    "BenchmarkEngine", "BenchmarkRun", "BenchmarkSpec", "RepetitionOutcome",
    "run_benchmark", "simulated_benchmark",
    # faults
    "SCENARIO_NAMES", "FaultScenario", "FaultScenarioResult", "ProbeEvent",
    "run_fault_scenario",
    # service
    "run_full_diagnostics",
    # harness
    "capture_baseline_measurement", "compare_baseline", "feature_b_side_by_side",
    "hysteresis_sweep", "scheduler_comparison",
    # report
    "compare_runs", "measurement_report", "run_data_kind", "run_to_csv",
    "run_to_json", "runs_to_csv",
    # store
    "DiagnosticsStore", "diagnostics_store", "persist_run",
]
