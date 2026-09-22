"""Tests for atp.storage.messages."""

import time
from pathlib import Path

import pytest

from atp.core.errors import StorageError
from atp.core.message import ATPMessage
from atp.storage.messages import MessageStatus, MessageStore, StoredMessage


def _make_message(nonce: str = "msg-test123", from_id: str = "alice@a.com", to_id: str = "bob@b.com") -> ATPMessage:
    return ATPMessage(
        from_id=from_id,
        to_id=to_id,
        timestamp=int(time.time()),
        nonce=nonce,
        type="message",
        payload={"body": "hello"},
    )


class TestMessageStore:
    def test_init_db_creates_table(self, tmp_path: Path) -> None:
        """init_db() should create the messages table."""
        store = MessageStore(db_path=tmp_path / "test.db")

        # Verify table exists by querying it
        result = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchone()
        assert result is not None

    def test_enqueue_and_get_by_nonce(self, tmp_path: Path) -> None:
        """enqueue() and get_by_nonce() should round-trip a message."""
        store = MessageStore(db_path=tmp_path / "test.db")
        msg = _make_message()

        row_id = store.enqueue(msg)

        assert row_id > 0
        stored = store.get_by_nonce("msg-test123")
        assert stored is not None
        assert stored.nonce == "msg-test123"
        assert stored.from_id == "alice@a.com"
        assert stored.to_id == "bob@b.com"
        assert stored.status == MessageStatus.QUEUED
        assert stored.retry_count == 0
        assert stored.error is None

    def test_duplicate_nonce_raises(self, tmp_path: Path) -> None:
        """enqueue() should raise StorageError on duplicate nonce."""
        db_path = tmp_path / "test.db"
        store = MessageStore(db_path=db_path)
        msg1 = _make_message(nonce="dup-nonce")
        msg2 = _make_message(nonce="dup-nonce")

        store.enqueue(msg1)

        with pytest.raises(StorageError):
            store.enqueue(msg2)

        # A rejected enqueue must not leave a write lock behind.
        second_store = MessageStore(db_path=db_path)
        row_id = second_store.enqueue(_make_message(nonce="fresh-nonce"))
        assert row_id > 0

    def test_sqlite_connection_uses_wal_and_busy_timeout(self, tmp_path: Path) -> None:
        store = MessageStore(db_path=tmp_path / "test.db")
        assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000

    def test_update_status(self, tmp_path: Path) -> None:
        """update_status() should change the message status."""
        store = MessageStore(db_path=tmp_path / "test.db")
        msg = _make_message()
        store.enqueue(msg)

        store.update_status("msg-test123", MessageStatus.DELIVERED)

        stored = store.get_by_nonce("msg-test123")
        assert stored is not None
        assert stored.status == MessageStatus.DELIVERED

    def test_update_status_with_error(self, tmp_path: Path) -> None:
        """update_status() should set error string when provided."""
        store = MessageStore(db_path=tmp_path / "test.db")
        msg = _make_message()
        store.enqueue(msg)

        store.update_status("msg-test123", MessageStatus.FAILED, error="connection refused")

        stored = store.get_by_nonce("msg-test123")
        assert stored is not None
        assert stored.status == MessageStatus.FAILED
        assert stored.error == "connection refused"

    def test_get_pending_deliveries_returns_queued(self, tmp_path: Path) -> None:
        """get_pending_deliveries() should return messages with status=queued."""
        store = MessageStore(db_path=tmp_path / "test.db")
        msg1 = _make_message(nonce="q1")
        msg2 = _make_message(nonce="q2")
        msg3 = _make_message(nonce="delivered1")

        store.enqueue(msg1)
        store.enqueue(msg2)
        store.enqueue(msg3, status=MessageStatus.DELIVERED)

        pending = store.get_pending_deliveries()

        nonces = {m.nonce for m in pending}
        assert "q1" in nonces
        assert "q2" in nonces
        assert "delivered1" not in nonces

    def test_get_pending_deliveries_includes_failed_with_retry(self, tmp_path: Path) -> None:
        """get_pending_deliveries() should include failed messages whose retry time has passed."""
        store = MessageStore(db_path=tmp_path / "test.db")
        msg = _make_message(nonce="retry1")
        store.enqueue(msg)

        # Mark for retry in the past
        store.mark_retry("retry1", next_retry_at=int(time.time()) - 10)

        pending = store.get_pending_deliveries()
        nonces = {m.nonce for m in pending}
        assert "retry1" in nonces

    def test_get_messages_for_agent(self, tmp_path: Path) -> None:
        """get_messages_for_agent() should return DELIVERED messages for a specific agent."""
        store = MessageStore(db_path=tmp_path / "test.db")

        msg1 = _make_message(nonce="m1", to_id="bob@b.com")
        msg2 = _make_message(nonce="m2", to_id="bob@b.com")
        msg3 = _make_message(nonce="m3", to_id="charlie@c.com")

        store.enqueue(msg1)
        store.enqueue(msg2)
        store.enqueue(msg3)

        # Only msg1 is delivered to bob
        store.update_status("m1", MessageStatus.DELIVERED)
        store.update_status("m2", MessageStatus.QUEUED)  # stays queued
        store.update_status("m3", MessageStatus.DELIVERED)  # different agent

        messages = store.get_messages_for_agent("bob@b.com")

        assert len(messages) == 1
        assert messages[0].nonce == "m1"

    def test_get_messages_for_agent_with_after_id(self, tmp_path: Path) -> None:
        """get_messages_for_agent() with after_id should only return later messages."""
        store = MessageStore(db_path=tmp_path / "test.db")

        msg1 = _make_message(nonce="a1", to_id="bob@b.com")
        msg2 = _make_message(nonce="a2", to_id="bob@b.com")

        id1 = store.enqueue(msg1)
        store.enqueue(msg2)

        store.update_status("a1", MessageStatus.DELIVERED)
        store.update_status("a2", MessageStatus.DELIVERED)

        messages = store.get_messages_for_agent("bob@b.com", after_id=id1)

        assert len(messages) == 1
        assert messages[0].nonce == "a2"

    def test_mark_retry_increments_count(self, tmp_path: Path) -> None:
        """mark_retry() should increment retry_count and set next_retry_at."""
        store = MessageStore(db_path=tmp_path / "test.db")
        msg = _make_message(nonce="r1")
        store.enqueue(msg)

        future = int(time.time()) + 60
        store.mark_retry("r1", next_retry_at=future)

        stored = store.get_by_nonce("r1")
        assert stored is not None
        assert stored.retry_count == 1
        assert stored.next_retry_at == future
        assert stored.status == MessageStatus.FAILED

        # Retry again
        store.mark_retry("r1", next_retry_at=future + 120)
        stored = store.get_by_nonce("r1")
        assert stored is not None
        assert stored.retry_count == 2

    def test_cleanup_expired(self, tmp_path: Path) -> None:
        """cleanup_expired() should remove old messages."""
        store = MessageStore(db_path=tmp_path / "test.db")
        msg = _make_message(nonce="old1")
        store.enqueue(msg)

        # Manually set created_at to long ago
        store._conn.execute(
            "UPDATE messages SET created_at = ? WHERE nonce = ?",
            (int(time.time()) - 86400 * 30, "old1"),
        )
        store._conn.commit()

        # Add a recent message
        msg2 = _make_message(nonce="new1")
        store.enqueue(msg2)

        deleted = store.cleanup_expired(max_age_seconds=86400 * 7)

        assert deleted == 1
        assert store.get_by_nonce("old1") is None
        assert store.get_by_nonce("new1") is not None

    def test_get_by_nonce_returns_none_for_unknown(self, tmp_path: Path) -> None:
        """get_by_nonce() should return None for a nonexistent nonce."""
        store = MessageStore(db_path=tmp_path / "test.db")

        result = store.get_by_nonce("does-not-exist")
        assert result is None
