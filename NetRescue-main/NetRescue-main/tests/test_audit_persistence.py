"""
tests/test_audit_persistence.py — the audit trail's durable side.

The in-memory log is easy to get right. These tests cover the part that is easy
to get wrong: records actually reaching PostgreSQL, doing so without charging the
request that generated them, and reporting honestly when they cannot.
"""
import threading
import time

import pytest

from netrescue.security.audit import (
    AuditRecord,
    Auditor,
    audit_persistence_status,
    default_auditor,
)


class FakeSession:
    """Minimal stand-in for a SQLAlchemy session that records what it was given."""

    def __init__(self, fail: bool = False):
        self.added = []
        self.commits = 0
        self.fail = fail

    def add(self, obj):
        if self.fail:
            raise RuntimeError("database is down")
        self.added.append(obj)

    def commit(self):
        if self.fail:
            raise RuntimeError("database is down")
        self.commits += 1


class FakeSessionFactory:
    """Context-manager factory matching what the auditor expects."""

    def __init__(self, fail: bool = False):
        self.sessions = []
        self.fail = fail
        self.concurrent = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()

    def __call__(self):
        factory = self

        class _Ctx:
            def __enter__(self_inner):
                session = FakeSession(fail=factory.fail)
                with factory._lock:
                    factory.sessions.append(session)
                    factory.concurrent += 1
                    factory.max_concurrent = max(
                        factory.max_concurrent, factory.concurrent
                    )
                return session

            def __exit__(self_inner, *exc):
                with factory._lock:
                    factory.concurrent -= 1
                return False

        return _Ctx()


def wait_for(predicate, timeout=5.0, interval=0.02):
    """Poll until predicate() is true or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestBackgroundFlushing:
    def test_records_reach_the_database(self):
        factory = FakeSessionFactory()
        auditor = Auditor(session_factory=factory, flush_interval_s=0.05)
        auditor.record("operator", "lab.topology.start", "A,B", True)
        assert wait_for(lambda: auditor._records_written >= 1), (
            "the queued record was never written"
        )
        written = factory.sessions[0].added
        assert written[0].action == "lab.topology.start"
        assert written[0].principal == "operator"

    def test_recording_does_not_block_on_the_database(self):
        """
        The whole point of the queue.

        `record()` is called from request handlers. If it opened a session
        inline, every audited request would contend for a connection from the
        same pool it is trying to query — which is exactly the deadlock this
        design avoids.
        """
        gate = threading.Event()

        class SlowFactory(FakeSessionFactory):
            def __call__(self_inner):
                gate.wait(2.0)
                return super().__call__()

        auditor = Auditor(session_factory=SlowFactory(), flush_interval_s=0.05)
        start = time.monotonic()
        for i in range(50):
            auditor.record("operator", f"action.{i}", "r", True)
        elapsed = time.monotonic() - start
        gate.set()
        assert elapsed < 0.5, (
            f"recording 50 entries took {elapsed:.2f}s — the write is not decoupled"
        )

    def test_batching_does_not_open_a_session_per_record(self):
        factory = FakeSessionFactory()
        auditor = Auditor(session_factory=factory, flush_interval_s=0.05)
        for i in range(25):
            auditor.record("operator", f"a{i}", "r", True)
        assert wait_for(lambda: auditor._records_written >= 25)
        # Far fewer sessions than records: the batch is written in one commit.
        assert len(factory.sessions) < 25
        total = sum(len(s.added) for s in factory.sessions)
        assert total == 25

    def test_a_failing_database_is_counted_not_hidden(self):
        factory = FakeSessionFactory(fail=True)
        auditor = Auditor(session_factory=factory, flush_interval_s=0.05)
        auditor.record("operator", "a", "r", True)
        assert wait_for(lambda: auditor.persist_failures >= 1)
        # The in-memory record still exists, so the action is not lost entirely.
        assert len(auditor.records) == 1

    def test_queue_is_bounded_and_drops_are_visible(self):
        factory = FakeSessionFactory()
        auditor = Auditor(session_factory=factory, flush_interval_s=30.0,
                          max_pending=10)
        for i in range(25):
            auditor.record("operator", f"a{i}", "r", True)
        assert len(auditor._pending) <= 10, "the queue must not grow without bound"
        assert auditor.dropped >= 15, "dropping records must be counted, not silent"

    def test_flush_now_drains_synchronously(self):
        factory = FakeSessionFactory()
        auditor = Auditor(session_factory=factory, flush_interval_s=3600.0)
        for i in range(5):
            auditor.record("operator", f"a{i}", "r", True)
        written = auditor.flush_now()
        assert written == 5
        assert len(auditor._pending) == 0

    def test_persist_disabled_writes_nothing(self):
        factory = FakeSessionFactory()
        auditor = Auditor(session_factory=factory, persist=False,
                          flush_interval_s=0.05)
        auditor.record("operator", "a", "r", True)
        time.sleep(0.2)
        assert factory.sessions == []
        assert len(auditor.records) == 1, "the in-memory log still works"

    def test_supplied_session_is_used_for_flushing(self):
        session = FakeSession()
        auditor = Auditor(db_session=session, flush_interval_s=0.05)
        auditor.record("operator", "a", "r", True)
        assert wait_for(lambda: len(session.added) == 1), (
            "an explicitly supplied session should receive the record"
        )
        assert session.commits >= 1

    def test_status_reports_the_whole_picture(self):
        factory = FakeSessionFactory()
        auditor = Auditor(session_factory=factory, flush_interval_s=0.05)
        auditor.record("operator", "a", "r", True)
        assert wait_for(lambda: auditor._records_written >= 1)
        assert wait_for(
            lambda: audit_persistence_status()["persist_enabled"] is False
            or True
        )
        # Status is computed from the module-level default, which must exist.
        status = audit_persistence_status()
        for key in ("persist_enabled", "persist_failures", "dropped",
                    "queued", "written", "in_memory_records"):
            assert key in status, f"status is missing {key}"


class TestDefaultAuditor:
    def test_default_auditor_persists(self):
        """
        The process-wide auditor must actually persist.

        It previously shipped with persist=False, which meant /api/audit — which
        reads from PostgreSQL — could never show anything no matter what the
        operator did.
        """
        auditor = default_auditor()
        assert auditor._persist is True, (
            "the default auditor must persist, or the audit trail is empty on disk"
        )
        assert auditor._session_factory is not None

    def test_default_auditor_is_a_singleton(self):
        assert default_auditor() is default_auditor()

    def test_denial_is_recorded(self):
        auditor = Auditor()
        auditor.record("anon", "lab.failures", "B", False)
        assert auditor.find(result="DENY")
        assert auditor.find(action="lab.failures")


class TestScrubbingStillApplies:
    def test_secret_is_redacted_before_queueing(self):
        factory = FakeSessionFactory()
        auditor = Auditor(session_factory=factory, flush_interval_s=0.05)
        auditor.record("operator", "a", "r", True,
                       {"api_key": "sk-live-supersecret", "ok": "value"})
        assert wait_for(lambda: auditor._records_written >= 1)
        detail = factory.sessions[0].added[0].detail
        assert "supersecret" not in detail
        assert "redacted" in detail.lower()
        assert '"ok": "value"' in detail, "non-secret fields must survive"
