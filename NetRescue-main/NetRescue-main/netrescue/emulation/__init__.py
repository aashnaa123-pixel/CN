"""
netrescue.emulation — Feature D: Linux tc/netem emulation (PRD §6).

  platform : capability detection, with actionable errors on non-Linux hosts
  tc_netem : the tc/netem parameter model, apply, remove and verify
  harness  : run lifecycle, fault injection, and emulation validation
"""

from netrescue.emulation.harness import (
    EmulationHarness,
    EmulationRecord,
    FaultInjector,
    ValidationResult,
    environment_report,
    validate_config,
)
from netrescue.emulation.platform import (
    EmulationSupport,
    EmulationUnavailable,
    emulation_support,
    is_emulation_available,
    require_emulation,
)
from netrescue.emulation.tc_netem import TcNetemConfig, apply_config, remove_config, verify_config

__all__ = [
    "EmulationHarness",
    "EmulationRecord",
    "FaultInjector",
    "ValidationResult",
    "environment_report",
    "validate_config",
    "EmulationSupport",
    "EmulationUnavailable",
    "emulation_support",
    "is_emulation_available",
    "require_emulation",
    "TcNetemConfig",
    "apply_config",
    "remove_config",
    "verify_config",
]