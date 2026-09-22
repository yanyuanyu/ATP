"""ATP agent credentials store — SQLite-backed agent registration and verification."""

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from atp.core.errors import ATPErrorCode, StorageError


@dataclass
class AgentRecord:
    agent_id: str
    created_at: int


class AgentStore:
    """Manages agent credentials in SQLite."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), timeout=30)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=30000")
        return self._conn

    def init_db(self) -> None:
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        conn.commit()

    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        """Hash password with salt using SHA-256 + PBKDF2."""
        return hashlib.pbkdf2_hmac(
            'sha256', password.encode(), salt.encode(), 100000
        ).hex()

    def register(self, agent_id: str, password: str) -> AgentRecord:
        """Register a new agent. Raises StorageError if already exists."""
        salt = os.urandom(32).hex()
        password_hash = self._hash_password(password, salt)
        now = int(time.time())
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO agents (agent_id, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                (agent_id, password_hash, salt, now)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            raise StorageError(
                code=ATPErrorCode.SERVER_ERROR,
                message=f"Agent {agent_id} already registered"
            )
        return AgentRecord(agent_id=agent_id, created_at=now)

    def verify(self, agent_id: str, password: str) -> bool:
        """Verify agent credentials. Returns True if valid."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT password_hash, salt FROM agents WHERE agent_id = ?",
            (agent_id,)
        ).fetchone()
        if row is None:
            return False
        stored_hash, salt = row
        return self._hash_password(password, salt) == stored_hash

    def list_agents(self) -> list[AgentRecord]:
        """List all registered agents."""
        conn = self._get_conn()
        rows = conn.execute("SELECT agent_id, created_at FROM agents ORDER BY created_at").fetchall()
        return [AgentRecord(agent_id=r[0], created_at=r[1]) for r in rows]

    def remove(self, agent_id: str) -> bool:
        """Remove an agent. Returns True if found and removed."""
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
        conn.commit()
        return cursor.rowcount > 0

    def change_password(self, agent_id: str, new_password: str) -> bool:
        """Change agent password. Returns True if agent exists."""
        conn = self._get_conn()
        row = conn.execute("SELECT agent_id FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
        if row is None:
            return False
        salt = os.urandom(32).hex()
        password_hash = self._hash_password(new_password, salt)
        conn.execute(
            "UPDATE agents SET password_hash = ?, salt = ? WHERE agent_id = ?",
            (password_hash, salt, agent_id)
        )
        conn.commit()
        return True
