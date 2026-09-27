"""ATP message store — SQLite-backed queue for message delivery."""

import json
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from atp.core.errors import ATPErrorCode, StorageError
from atp.core.message import ATPMessage
from atp.core.identity import AgentID


class MessageStatus(Enum):
    QUEUED = "queued"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    FAILED = "failed"
    BOUNCED = "bounced"


@dataclass
class StoredMessage:
    id: int
    nonce: str
    from_id: str
    to_id: str
    message_json: str
    status: MessageStatus
    created_at: int
    updated_at: int
    retry_count: int
    next_retry_at: int | None
    error: str | None


class MessageStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self.init_db()

    def init_db(self) -> None:
        """Create the messages table if it does not exist."""
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nonce TEXT UNIQUE NOT NULL,
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                message_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                next_retry_at INTEGER,
                error TEXT
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS outbound_wire (
                nonce TEXT PRIMARY KEY, message_json TEXT NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS transfer_receipts (
                nonce TEXT PRIMARY KEY, digest TEXT NOT NULL
            )"""
        )
        self._conn.commit()

    def get_wire(self, nonce: str) -> ATPMessage | None:
        row = self._conn.execute(
            "SELECT message_json FROM outbound_wire WHERE nonce=?", (nonce,)
        ).fetchone()
        return ATPMessage.from_json(row[0]) if row else None

    def save_wire(self, message: ATPMessage) -> ATPMessage:
        # Commit ciphertext BEFORE sending; retries after a crash reuse it.
        self._conn.execute(
            "INSERT OR IGNORE INTO outbound_wire VALUES (?, ?)",
            (message.nonce, message.to_json()),
        )
        self._conn.commit()
        saved = self.get_wire(message.nonce)
        assert saved is not None
        return saved

    def accept_transfer(self, message: ATPMessage, digest: str) -> bool:
        """Atomically insert inbox and durable receipt. False means identical retry.

        Receipts deliberately outlive mailbox cleanup; never redeliver a paid task
        merely because an old inbox row was removed.
        """
        with self._conn:
            receipt = self._conn.execute(
                "SELECT digest FROM transfer_receipts WHERE nonce=?", (message.nonce,)
            ).fetchone()
            if receipt:
                if receipt[0] != digest:
                    raise ValueError("Business nonce reused with different content")
                return False
            existing = self.get_by_nonce(message.nonce)
            if existing:
                from atp.security.transfer import message_digest
                if (existing.status != MessageStatus.DELIVERED or
                        message_digest(ATPMessage.from_json(existing.message_json)) != digest):
                    raise ValueError("Conflicting business nonce")
            else:
                now = int(time.time())
                self._conn.execute(
                    """INSERT INTO messages
                       (nonce,from_id,to_id,message_json,status,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (message.nonce, message.from_id, str(AgentID.parse(message.to_id)), message.to_json(),
                     MessageStatus.DELIVERED.value, now, now),
                )
            self._conn.execute(
                "INSERT INTO transfer_receipts VALUES (?,?)", (message.nonce, digest)
            )
            return existing is None

    def has_transfer_receipt(self, nonce: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM transfer_receipts WHERE nonce=?", (nonce,)
        ).fetchone() is not None

    def get_fair_pending(self, excluded_domains: set[str], limit: int = 4) -> list[StoredMessage]:
        """One oldest ready message per domain, excluding active destinations."""
        placeholders = ",".join("?" for _ in excluded_domains) or "NULL"
        exclusion = f"AND domain NOT IN ({placeholders})" if excluded_domains else ""
        rows = self._conn.execute(
            f"""WITH ready AS (
                SELECT *, lower(substr(to_id,instr(to_id,'@')+1)) AS domain
                FROM messages WHERE status='queued'
                OR (status='failed' AND next_retry_at<=?)
            ), ranked AS (
                SELECT *, row_number() OVER
                (PARTITION BY domain ORDER BY created_at,id) AS position
                FROM ready WHERE 1=1 {exclusion}
            )
            SELECT * FROM ranked WHERE position=1 ORDER BY updated_at,id LIMIT ?""",
            (int(time.time()), *sorted(excluded_domains), limit),
        ).fetchall()
        return [self._row_to_stored(row) for row in rows]

    def _row_to_stored(self, row: sqlite3.Row) -> StoredMessage:
        return StoredMessage(
            id=row["id"],
            nonce=row["nonce"],
            from_id=row["from_id"],
            to_id=row["to_id"],
            message_json=row["message_json"],
            status=MessageStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            retry_count=row["retry_count"],
            next_retry_at=row["next_retry_at"],
            error=row["error"],
        )

    def enqueue(
        self, message: ATPMessage, status: MessageStatus = MessageStatus.QUEUED
    ) -> int:
        """Insert message. Return rowid. Raise StorageError on duplicate nonce."""
        now = int(time.time())
        message_json = message.to_json()
        try:
            cursor = self._conn.execute(
                """INSERT INTO messages
                   (nonce, from_id, to_id, message_json, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    message.nonce,
                    message.from_id,
                    message.to_id,
                    message_json,
                    status.value,
                    now,
                    now,
                ),
            )
            self._conn.commit()
            return cursor.lastrowid  # type: ignore[return-value]
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            raise StorageError(
                ATPErrorCode.SERVER_ERROR,
                f"Duplicate nonce: {message.nonce}",
            ) from exc

    def get_by_nonce(self, nonce: str) -> StoredMessage | None:
        """Retrieve a stored message by its nonce."""
        cursor = self._conn.execute(
            "SELECT * FROM messages WHERE nonce = ?", (nonce,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_stored(row)

    def update_status(
        self, nonce: str, status: MessageStatus, error: str | None = None
    ) -> None:
        """Update status and updated_at. Optionally set error."""
        now = int(time.time())
        self._conn.execute(
            """UPDATE messages
               SET status = ?, updated_at = ?, error = ?
               WHERE nonce = ?""",
            (status.value, now, error, nonce),
        )
        self._conn.commit()

    def recover_interrupted_deliveries(self) -> int:
        """Resume in-flight sends after process restart, preserving identity/nonce.

        Only call while starting the single delivery worker for this database.
        A remote receipt may already exist; authenticated retries consult it.
        """
        cursor = self._conn.execute(
            "UPDATE messages SET status='queued', updated_at=? WHERE status='delivering'",
            (int(time.time()),),
        )
        recovered = cursor.rowcount
        # Migrate pending timers from the former minutes/hours backoff without
        # changing retry counts or reviving delivered/bounced messages.
        self._conn.execute(
            "UPDATE messages SET next_retry_at=? WHERE status='failed' AND next_retry_at>?",
            (int(time.time()) + 10, int(time.time()) + 10),
        )
        self._conn.commit()
        return recovered

    def get_pending_deliveries(self, limit: int = 50) -> list[StoredMessage]:
        """Get messages where status='queued' or (status='failed' and retry ready)."""
        now = int(time.time())
        cursor = self._conn.execute(
            """SELECT * FROM messages
               WHERE status = 'queued'
                  OR (status = 'failed' AND next_retry_at IS NOT NULL AND next_retry_at <= ?)
               ORDER BY created_at
               LIMIT ?""",
            (now, limit),
        )
        return [self._row_to_stored(row) for row in cursor.fetchall()]

    def get_messages_for_agent(
        self, agent_id: str, limit: int = 50, after_id: int | None = None
    ) -> list[StoredMessage]:
        """Get DELIVERED messages where to_id=agent_id, optionally after a given id."""
        if after_id is not None:
            cursor = self._conn.execute(
                """SELECT * FROM messages
                   WHERE to_id = ? AND status = 'delivered' AND id > ?
                   ORDER BY id
                   LIMIT ?""",
                (agent_id, after_id, limit),
            )
        else:
            cursor = self._conn.execute(
                """SELECT * FROM messages
                   WHERE to_id = ? AND status = 'delivered'
                   ORDER BY id
                   LIMIT ?""",
                (agent_id, limit),
            )
        return [self._row_to_stored(row) for row in cursor.fetchall()]

    def mark_retry(self, nonce: str, next_retry_at: int) -> None:
        """Increment retry_count, set next_retry_at, set status='failed', update updated_at."""
        now = int(time.time())
        self._conn.execute(
            """UPDATE messages
               SET retry_count = retry_count + 1,
                   next_retry_at = ?,
                   status = 'failed',
                   updated_at = ?
               WHERE nonce = ?""",
            (next_retry_at, now, nonce),
        )
        self._conn.commit()

    def cleanup_expired(self, max_age_seconds: int = 86400 * 7) -> int:
        """Delete messages older than max_age. Return count deleted."""
        cutoff = int(time.time()) - max_age_seconds
        cursor = self._conn.execute(
            "DELETE FROM messages WHERE created_at < ?", (cutoff,)
        )
        self._conn.commit()
        return cursor.rowcount
