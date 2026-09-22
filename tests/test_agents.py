"""Tests for ATP agent credentials store."""

import pytest

from atp.core.errors import StorageError
from atp.storage.agents import AgentStore


@pytest.fixture
def store(tmp_path):
    s = AgentStore(tmp_path / "test_agents.db")
    s.init_db()
    return s


class TestAgentStore:
    def test_register_and_verify(self, store):
        record = store.register("alice@example.com", "secret123")
        assert record.agent_id == "alice@example.com"
        assert record.created_at > 0
        assert store.verify("alice@example.com", "secret123") is True

    def test_verify_wrong_password(self, store):
        store.register("alice@example.com", "secret123")
        assert store.verify("alice@example.com", "wrongpass") is False

    def test_verify_unknown_agent(self, store):
        assert store.verify("unknown@example.com", "anything") is False

    def test_list_agents(self, store):
        store.register("alice@example.com", "pass1")
        store.register("bob@example.com", "pass2")
        agents = store.list_agents()
        assert len(agents) == 2
        ids = [a.agent_id for a in agents]
        assert "alice@example.com" in ids
        assert "bob@example.com" in ids

    def test_remove_agent(self, store):
        store.register("alice@example.com", "secret")
        assert store.remove("alice@example.com") is True
        assert store.verify("alice@example.com", "secret") is False
        assert store.remove("alice@example.com") is False

    def test_duplicate_register_raises_storage_error(self, store):
        store.register("alice@example.com", "secret")
        with pytest.raises(StorageError):
            store.register("alice@example.com", "different")

        # A rejected registration must not leave a write lock behind.
        second_store = AgentStore(store._db_path)
        second_store.init_db()
        record = second_store.register("bob@example.com", "secret")
        assert record.agent_id == "bob@example.com"

    def test_sqlite_connection_uses_wal_and_busy_timeout(self, store):
        conn = store._get_conn()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000

    def test_change_password(self, store):
        store.register("alice@example.com", "oldpass")
        assert store.verify("alice@example.com", "oldpass") is True

        result = store.change_password("alice@example.com", "newpass")
        assert result is True
        assert store.verify("alice@example.com", "oldpass") is False
        assert store.verify("alice@example.com", "newpass") is True

    def test_change_password_nonexistent(self, store):
        assert store.change_password("nobody@example.com", "pass") is False

    def test_list_empty(self, store):
        agents = store.list_agents()
        assert agents == []
