"""
netrescue/diagnostics/config.py

Normalised configuration snapshots and their SHA-256 (Feature 3 §H).

A benchmark run must save a normalised snapshot of everything that determined
it, then hash that snapshot before execution and carry the hash in the report.
The hash establishes **configuration identity** — "these two runs were set up
the same way" — it is *not* evidence that the experiment was executed
correctly, measured anything meaningful, or produced a valid result. Two runs
with the same hash can still disagree because the network changed underneath
them.

`normalise` makes the hash order-independent for objects (key order does not
matter) but order-preserving for lists (path order and sweep-ratio order are
part of the configuration).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: Software version recorded with every report. Kept here so the API, the
#: runner and the reports all cite one value. The FastAPI app also advertises
#: 1.0.0 (apps/api/main.py); they are intentionally the same release line.
SOFTWARE_VERSION = "1.0.0"


def normalise(snapshot: Any) -> Any:
    """
    Canonical, JSON-safe, order-independent form of a configuration value.

    Objects are sorted by key. Lists keep their order. Floats that are whole
    are kept as floats except for negative zero, which is folded to ``0.0`` so
    that ``-0.0`` and ``0.0`` do not hash differently.
    """
    if isinstance(snapshot, dict):
        return {
            str(key): normalise(value)
            for key, value in sorted(snapshot.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(snapshot, (list, tuple)):
        return [normalise(item) for item in snapshot]
    if isinstance(snapshot, bool) or snapshot is None:
        return snapshot
    if isinstance(snapshot, float):
        return 0.0 if snapshot == 0.0 else snapshot
    if isinstance(snapshot, int):
        return int(snapshot)
    return snapshot


def canonical_json(snapshot: Any) -> str:
    """The exact byte string that gets hashed — exposed for tests and docs."""
    return json.dumps(
        normalise(snapshot),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def config_hash(snapshot: Any) -> str:
    """SHA-256 of the normalised configuration snapshot (Feature 3 §H)."""
    return hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()
