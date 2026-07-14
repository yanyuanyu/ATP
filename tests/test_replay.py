import time
import threading
import uuid

import pytest

from atp.security.replay import ReplayGuard


class TestReplayGuard:
    def test_fresh_message_returns_true(self):
        guard = ReplayGuard()
        nonce = str(uuid.uuid4())
        timestamp = int(time.time())
        assert guard.check(nonce, timestamp) is True

    def test_same_nonce_returns_false(self):
        guard = ReplayGuard()
        nonce = str(uuid.uuid4())
        timestamp = int(time.time())
        assert guard.check(nonce, timestamp) is True
        assert guard.check(nonce, timestamp) is False

    def test_same_nonce_from_different_senders_is_allowed(self):
        guard = ReplayGuard()
        timestamp = int(time.time())
        assert guard.check("shared", timestamp, sender="a@example.com") is True
        assert guard.check("shared", timestamp, sender="b@example.com") is True
        assert guard.check("shared", timestamp, sender="a@example.com") is False

    def test_expired_timestamp_returns_false(self):
        guard = ReplayGuard(max_age_seconds=300)
        nonce = str(uuid.uuid4())
        old_timestamp = int(time.time()) - 600  # 10 minutes ago
        assert guard.check(nonce, old_timestamp) is False

    def test_future_timestamp_within_60s_returns_true(self):
        guard = ReplayGuard()
        nonce = str(uuid.uuid4())
        future_timestamp = int(time.time()) + 30  # 30 seconds in future
        assert guard.check(nonce, future_timestamp) is True

    def test_far_future_timestamp_returns_false(self):
        guard = ReplayGuard()
        nonce = str(uuid.uuid4())
        far_future = int(time.time()) + 120  # 2 minutes in future
        assert guard.check(nonce, far_future) is False

    def test_cache_eviction_on_max_size(self):
        guard = ReplayGuard(max_cache_size=5)
        now = int(time.time())
        nonces = [str(uuid.uuid4()) for _ in range(6)]

        # Fill cache beyond max_size
        for nonce in nonces:
            assert guard.check(nonce, now) is True

        # The first nonce should have been evicted, so it should be accepted again
        assert guard.check(nonces[0], now) is True

        # The last nonce should still be in cache (not evicted)
        assert guard.check(nonces[-1], now) is False

    def test_clear_empties_cache(self):
        guard = ReplayGuard()
        nonce = str(uuid.uuid4())
        timestamp = int(time.time())
        assert guard.check(nonce, timestamp) is True
        assert guard.check(nonce, timestamp) is False  # replay

        guard.clear()

        # After clearing, same nonce should be accepted again
        assert guard.check(nonce, timestamp) is True

    def test_thread_safety(self):
        guard = ReplayGuard()
        now = int(time.time())
        results = []
        errors = []

        def worker(thread_id: int):
            try:
                for i in range(100):
                    nonce = f"thread-{thread_id}-nonce-{i}"
                    result = guard.check(nonce, now)
                    results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        # All nonces are unique per thread, so all should be True
        assert all(r is True for r in results)
        assert len(results) == 1000
