"""
netrescue/security/hmac_auth.py

Message authentication for control-plane traffic (PRD §9).

Provides the authenticity and integrity primitives the PRD requires:

  * HMAC-SHA256 signing and verification of control messages
  * constant-time comparison
  * sequence numbers with a freshness window (replay protection)
  * never logging keys or MACs

PRD §9 rule 1 is explicit: HMAC gives authenticity and integrity, *not*
confidentiality. Anything secret in a message must additionally travel over a
TLS channel — see netrescue/security/tls.py.

This module is the single source of truth for that signing logic;
netrescue/recovery/heartbeat.py delegates here so the wire format is defined
once rather than duplicated.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: Default replay window. A message older than this is rejected outright.
DEFAULT_FRESHNESS_WINDOW_S = 30.0

#: Clock skew tolerated for messages dated slightly in the future.
DEFAULT_FUTURE_SKEW_S = 5.0

#: Sequence numbers retained per peer for replay detection.
DEFAULT_SEEN_CAPACITY = 1024


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def generate_key(nbytes: int = 32) -> bytes:
    """Generate a random symmetric key from the OS CSPRNG."""
    return secrets.token_bytes(nbytes)


def load_key(value: str | bytes | None) -> bytes:
    """
    Coerce a configured key into bytes.

    Accepts raw bytes, or a hex/base64 string from the environment. Never logs
    the result (PRD §9 — no secrets in logs).
    """
    if value is None:
        raise ValueError("no key configured")
    if isinstance(value, bytes):
        if not value:
            raise ValueError("empty key")
        return value
    text = value.strip()
    if not text:
        raise ValueError("empty key")
    try:
        return bytes.fromhex(text)
    except ValueError:
        pass
    try:
        import base64
        return base64.b64decode(text, validate=True)
    except Exception:
        # Treat as a passphrase; warn loudly that this is weaker.
        log.warning("[security] key is neither hex nor base64 — using raw UTF-8 bytes.")
        return text.encode("utf-8")


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def canonical_body(payload: dict[str, Any]) -> bytes:
    """
    Deterministic serialisation used as the MAC input.

    Keys are sorted so the sender and receiver always agree on byte order.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(payload: dict[str, Any], key: bytes) -> dict[str, Any]:
    """
    Return a copy of payload with a ``mac`` field appended.

    The MAC covers every other field, so any tampering — including changing the
    sequence number or timestamp — invalidates it.
    """
    body = canonical_body(payload)
    mac = hmac.new(key, body, hashlib.sha256).hexdigest()
    signed = dict(payload)
    signed["mac"] = mac
    return signed


def verify(payload: dict[str, Any], key: bytes) -> bool:
    """Constant-time MAC verification. Never logs the key or the MAC."""
    if "mac" not in payload:
        return False
    unsigned = {k: v for k, v in payload.items() if k != "mac"}
    expected = hmac.new(key, canonical_body(unsigned), hashlib.sha256).hexdigest()
    return hmac.compare_digest(str(payload["mac"]), expected)


# ---------------------------------------------------------------------------
# Replay protection
# ---------------------------------------------------------------------------


@dataclass
class ReplayGuard:
    """
    Rejects stale or repeated control messages (PRD §9).

    Two independent checks, both required:

      1. freshness — the message timestamp must be inside the window
      2. replay    — the sequence number must not have been seen before

    Either failure rejects the message; callers should treat a rejection as
    hostile or broken rather than as a transient error.
    """

    freshness_window_s: float = DEFAULT_FRESHNESS_WINDOW_S
    future_skew_s: float = DEFAULT_FUTURE_SKEW_S
    capacity: int = DEFAULT_SEEN_CAPACITY

    _seen: deque[int] = field(default_factory=deque, init=False)
    _seen_set: set[int] = field(default_factory=set, init=False)
    _last_seq: int = field(default=-1, init=False)

    # ------------------------------------------------------------------

    def check(self, payload: dict[str, Any], *, enforce_monotonic: bool = True) -> tuple[bool, str]:
        """
        Validate freshness and novelty.

        Returns ``(accepted, reason)``. ``reason`` is empty when accepted and is
        safe to log — it never contains key material.
        """
        ts = payload.get("ts")
        if not isinstance(ts, (int, float)):
            return False, "missing_or_invalid_timestamp"

        age = time.time() - float(ts)
        if age > self.freshness_window_s:
            return False, f"stale:{age:.1f}s"
        if age < -self.future_skew_s:
            return False, f"future:{age:.1f}s"

        seq = payload.get("seq")
        if not isinstance(seq, int):
            return False, "missing_or_invalid_sequence"

        # Replay check first: seeing a sequence number we already accepted is an
        # unambiguous replay. Monotonicity alone would also catch it, but would
        # report it as "non-monotonic", which reads like ordinary packet
        # reordering rather than an attack.
        if seq in self._seen_set:
            return False, f"replayed_seq:{seq}"

        if enforce_monotonic and seq <= self._last_seq:
            # A lower sequence number that was never seen is packet reordering
            # on a lossy UDP path, not a replay. It is still useless to us.
            return False, f"non_monotonic_seq:{seq}<={self._last_seq}"

        return True, ""

    # ------------------------------------------------------------------

    def accept(self, seq: int) -> None:
        """Record an accepted sequence number."""
        if seq in self._seen_set:
            return
        self._seen.append(seq)
        self._seen_set.add(seq)
        self._last_seq = max(self._last_seq, seq)
        while len(self._seen) > self.capacity:
            old = self._seen.popleft()
            self._seen_set.discard(old)

    def reset(self) -> None:
        self._seen.clear()
        self._seen_set.clear()
        self._last_seq = -1


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------


@dataclass
class Authenticator:
    """
    Signs outgoing messages and verifies incoming ones for one peer relationship.

    Usage::

        auth = Authenticator(key=load_key(os.environ["NODE_HMAC_KEY"]))
        signed = auth.sign({"sender_id": "A", "seq": 1, "ts": time.time()})

        ok, reason = auth.verify_incoming(signed)
        if not ok:
            log.warning("rejected: %s", reason)
    """

    key: bytes
    guard: ReplayGuard = field(default_factory=ReplayGuard)

    def sign(self, payload: dict[str, Any]) -> dict[str, Any]:
        return sign(payload, self.key)

    def verify_incoming(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """MAC check first, then replay checks. Returns (accepted, reason)."""
        if not verify(payload, self.key):
            return False, "invalid_mac"
        ok, reason = self.guard.check(payload)
        if not ok:
            return False, reason
        self.guard.accept(int(payload["seq"]))
        return True, ""

    def reset(self) -> None:
        self.guard.reset()