import sqlite3
import time
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional


class ReplayGuard:
    """Nonce cache with timestamp window. Thread-safe.

    Uses in-memory LRU cache for fast lookups, backed by optional SQLite
    persistence so nonces survive server restarts.

    SQLite-backed replay persistence is for restart recovery on a single
    server instance only. It does NOT provide cross-instance replay
    protection: concurrent instances sharing the same nonces.db will each
    maintain independent in-memory caches and may both accept the same
    nonce. Multi-instance deployments requiring shared replay protection
    should use a shared store (e.g. Redis) in front of this guard.
    """

    def __init__(
        self,
        max_age_seconds: int = 300,
        max_cache_size: int = 100_000,
        db_path: Optional[Path] = None,
        _prune_interval: int = 1000,
    ):
        self._max_age = max_age_seconds
        self._max_size = max_cache_size
        self._cache: OrderedDict[str, int] = OrderedDict()  # nonce -> timestamp
        self._lock = threading.Lock()
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._insert_count: int = 0  # Track inserts for periodic pruning
        self._prune_interval: int = _prune_interval

        if db_path is not None:
            self._init_db()
            self._load_from_db()

    def _init_db(self) -> None:
        """Create the nonces table if using persistent storage."""
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS nonces (
                nonce TEXT PRIMARY KEY,
                timestamp INTEGER NOT NULL
            )"""
        )
        self._conn.commit()

    def _load_from_db(self) -> None:
        """Load unexpired nonces from SQLite into memory on startup."""
        if self._conn is None:
            return
        now = int(time.time())
        cutoff = now - self._max_age
        cursor = self._conn.execute(
            "SELECT nonce, timestamp FROM nonces WHERE timestamp >= ? ORDER BY timestamp",
            (cutoff,),
        )
        with self._lock:
            for nonce, ts in cursor:
                self._cache[nonce] = ts
        # Purge expired entries from DB
        self._conn.execute("DELETE FROM nonces WHERE timestamp < ?", (cutoff,))
        self._conn.commit()

    def check(self, nonce: str, timestamp: int, sender: str = "") -> bool:
        """Returns True if fresh (not a replay), False if replay detected.

        Only consults the in-memory cache — does not re-read SQLite at
        runtime. This means replay detection is per-instance; see class
        docstring for multi-instance limitations.

        Checks:
        1. Is timestamp within [now - max_age, now + 60]? If not, return False.
        2. Is nonce already in cache? If yes, return False (replay).
        3. Add nonce to cache and persist. If cache exceeds max_size, evict oldest.
        4. Return True.
        """
        now = int(time.time())

        # 1. Check timestamp window
        if timestamp < now - self._max_age or timestamp > now + 60:
            return False

        cache_key = f"{sender}\0{nonce}" if sender else nonce

        with self._lock:
            # 2. Check for replay
            if cache_key in self._cache:
                return False

            # 3. Add nonce, evict oldest if needed
            self._cache[cache_key] = timestamp
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

        # 4. Persist to SQLite and periodically prune expired rows
        if self._conn is not None:
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO nonces (nonce, timestamp) VALUES (?, ?)",
                    (cache_key, timestamp),
                )
                self._insert_count += 1
                if self._insert_count >= self._prune_interval:
                    self._prune_db()
                    self._insert_count = 0
                self._conn.commit()
            except sqlite3.Error:
                pass  # Cache is authoritative; DB failure is non-fatal

        # 5. Fresh message
        return True

    def _prune_db(self) -> None:
        """Remove expired nonces from SQLite."""
        if self._conn is None:
            return
        cutoff = int(time.time()) - self._max_age
        try:
            self._conn.execute("DELETE FROM nonces WHERE timestamp < ?", (cutoff,))
        except sqlite3.Error:
            pass

    def clear(self) -> None:
        """Clear all cached nonces. For testing."""
        with self._lock:
            self._cache.clear()
        if self._conn is not None:
            self._conn.execute("DELETE FROM nonces")
            self._conn.commit()
