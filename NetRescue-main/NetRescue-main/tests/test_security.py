"""
tests/test_security.py — PRD §9 security controls.

Covers HMAC authenticity, replay protection, authorisation roles, audit
scrubbing, and the non-Linux emulation guard.
"""

import time

import pytest

from netrescue.emulation.platform import EmulationUnavailable, emulation_support
from netrescue.security.audit import REDACTED, Auditor, scrub
from netrescue.security.authz import (
    AuthorizationError,
    Authorizer,
    Permission,
    Principal,
    Role,
)
from netrescue.security.hmac_auth import (
    Authenticator,
    ReplayGuard,
    canonical_body,
    generate_key,
    load_key,
    sign,
    verify,
)


class TestHmac:
    def test_round_trip(self):
        key = generate_key()
        signed = sign({"seq": 1, "ts": time.time(), "link_id": "A-B"}, key)
        assert verify(signed, key)

    def test_tampering_breaks_the_mac(self):
        key = generate_key()
        signed = sign({"seq": 1, "ts": time.time()}, key)
        signed["seq"] = 999
        assert not verify(signed, key)

    def test_wrong_key_rejected(self):
        signed = sign({"seq": 1, "ts": time.time()}, generate_key())
        assert not verify(signed, generate_key())

    def test_missing_mac_rejected(self):
        assert not verify({"seq": 1}, generate_key())

    def test_canonical_body_is_key_order_independent(self):
        a = canonical_body({"b": 2, "a": 1})
        b = canonical_body({"a": 1, "b": 2})
        assert a == b

    def test_load_key_accepts_hex(self):
        raw = b"\x01\x02\x03"
        assert load_key(raw.hex()) == raw

    def test_load_key_rejects_empty(self):
        with pytest.raises(ValueError):
            load_key("")


class TestReplay:
    def test_accepts_fresh_message(self):
        g = ReplayGuard()
        payload = {"seq": 1, "ts": time.time()}
        ok, reason = g.check(payload)
        assert ok and reason == ""
        g.accept(1)

    def test_rejects_replay(self):
        g = ReplayGuard()
        payload = {"seq": 5, "ts": time.time()}
        assert g.check(payload)[0]
        g.accept(5)
        ok, reason = g.check(payload)
        assert not ok and "replay" in reason

    def test_rejects_stale(self):
        g = ReplayGuard(freshness_window_s=1.0)
        ok, reason = g.check({"seq": 1, "ts": time.time() - 60})
        assert not ok and "stale" in reason

    def test_rejects_far_future(self):
        g = ReplayGuard(future_skew_s=5.0)
        ok, reason = g.check({"seq": 1, "ts": time.time() + 600})
        assert not ok and "future" in reason

    def test_rejects_non_monotonic(self):
        g = ReplayGuard()
        g.check({"seq": 10, "ts": time.time()})
        g.accept(10)
        ok, reason = g.check({"seq": 5, "ts": time.time()})
        assert not ok and "non_monotonic" in reason

    def test_requires_sequence_number(self):
        g = ReplayGuard()
        ok, reason = g.check({"ts": time.time()})
        assert not ok and "sequence" in reason

    def test_capacity_is_bounded(self):
        g = ReplayGuard(capacity=4)
        for i in range(1, 12):
            g.check({"seq": i, "ts": time.time()})
            g.accept(i)
        assert len(g._seen) <= 4


class TestAuthenticator:
    def test_accepts_then_rejects_replay(self):
        auth = Authenticator(key=generate_key())
        payload = auth.sign({"seq": 1, "ts": time.time()})
        assert auth.verify_incoming(payload)[0]
        ok, reason = auth.verify_incoming(payload)
        assert not ok and reason != "invalid_mac"

    def test_forged_payload_rejected(self):
        auth = Authenticator(key=generate_key())
        ok, reason = auth.verify_incoming({"seq": 1, "ts": time.time(), "mac": "00" * 32})
        assert not ok and reason == "invalid_mac"


class TestAuthorisation:
    def test_readonly_cannot_write(self):
        az = Authorizer(auditor=Auditor(persist=False))
        p = Principal("dash", Role.READONLY)
        assert az.check(p, Permission.READ)
        assert not az.check(p, Permission.WRITE)

    def test_node_cannot_reshape_the_network(self):
        """PRD §9 rule 2/3 — a node identity has no control-plane power."""
        az = Authorizer()
        p = Principal("node-a", Role.NODE)
        for perm in (Permission.WRITE, Permission.APPLY_EMULATION, Permission.INJECT_FAULT):
            with pytest.raises(AuthorizationError):
                az.require(p, perm)

    def test_operator_can_write_and_emulate(self):
        az = Authorizer()
        p = Principal("ops", Role.OPERATOR)
        for perm in (Permission.WRITE, Permission.APPLY_EMULATION, Permission.INJECT_FAULT):
            az.require(p, perm)  # must not raise

    def test_denial_is_audited(self):
        auditor = Auditor(persist=False)
        az = Authorizer(auditor=auditor)
        with pytest.raises(AuthorizationError):
            az.require(Principal("node-a", Role.NODE), Permission.WRITE, "apply-emulation")
        denials = auditor.find(result="DENY")
        assert denials and denials[0].principal == "node-a"
        assert denials[0].action == "apply-emulation"


class TestAuditScrubbing:
    def test_secret_keys_are_redacted(self):
        out = scrub({"password": "hunter2", "hmac_key": "abc", "seq": 1})
        assert out["password"] == REDACTED
        assert out["hmac_key"] == REDACTED
        assert out["seq"] == 1

    def test_nested_secrets_are_redacted(self):
        out = scrub({"outer": {"api_token": "t0ken", "keep": "value"}})
        assert out["outer"]["api_token"] == REDACTED
        assert out["outer"]["keep"] == "value"

    def test_hash_like_strings_are_redacted(self):
        assert scrub("a" * 64) == REDACTED

    def test_long_values_are_truncated(self):
        assert len(scrub("x" * 5000)) < 600

    def test_audit_entry_scrubs_detail(self):
        auditor = Auditor(persist=False)
        rec = auditor.allow("ops", "apply", "eth0", {"private_key": "hunter2"})
        assert "hunter2" not in (rec.detail or "")

    def test_find_filters(self):
        auditor = Auditor(persist=False)
        auditor.allow("ops", "read", "a")
        auditor.deny("node", "read", "b", "nope")
        assert len(auditor.find(result="DENY")) == 1
        assert len(auditor.find(principal="ops")) == 1


class TestEmulationGuard:
    def test_report_has_actionable_fields(self):
        support = emulation_support()
        assert "is_linux" in support.to_dict()
        if not support.usable:
            assert support.reason, "an unusable host must explain why"
            assert support.hint, "and must suggest a fix"

    @pytest.mark.skipif(
        emulation_support().usable, reason="emulation genuinely available on this host"
    )
    def test_require_emulation_raises_with_hint(self):
        with pytest.raises(EmulationUnavailable) as exc:
            from netrescue.emulation.platform import require_emulation
            require_emulation()
        assert exc.value.hint

def test_role_header_needs_token_when_configured(monkeypatch):
    """With API_AUTH_TOKEN set, a forged role header alone is downgraded to readonly."""
    from starlette.requests import Request

    from apps.api import deps
    from netrescue.security.authz import Role

    monkeypatch.setattr(deps.settings, "api_auth_token", "s3cret")

    def req(headers):
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        return Request({"type": "http", "headers": raw})

    forged = req({"X-NetRescue-Role": "operator"})
    assert deps.resolve_principal(forged).role is Role.READONLY
    wrong = req({"X-NetRescue-Role": "operator", "X-NetRescue-Token": "nope"})
    assert deps.resolve_principal(wrong).role is Role.READONLY
    ok = req({"X-NetRescue-Role": "operator", "X-NetRescue-Token": "s3cret"})
    assert deps.resolve_principal(ok).role is Role.OPERATOR
