"""Authenticated local ATP mailbox; all domain transfers use ATP server delivery."""
import json
import os
from pathlib import Path

import httpx

from atp.client import ATPClient
from atp.core.message import ATPMessage
from atp.finance.ledger import connect


class Mailbox:
    def __init__(self, identity: str, server: str, root: Path):
        self.identity, self.server = identity, server
        self.password = os.environ["ATP_FINANCE_PASSWORD"]
        self.client = ATPClient(identity, server, password=self.password)
        self.ready = False
        root.mkdir(parents=True, exist_ok=True)
        self.db = root / "inbox.db"
        with connect(self.db) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS inbox(nonce TEXT PRIMARY KEY, body TEXT NOT NULL, processed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS cursor(id INTEGER PRIMARY KEY, value INTEGER);
            """)
            row = db.execute("SELECT value FROM cursor WHERE id=1").fetchone()
            if row:
                self.client._last_recv_id = row[0]

    async def register(self):
        async with httpx.AsyncClient(timeout=10) as client:
            url = self.client._base_url + "/.well-known/atp/v1"
            response = await client.post(url + "/register", json={"agent_id": self.identity, "password": self.password})
            if response.status_code not in (201, 409):
                response.raise_for_status()
            # A pre-existing account with another password must fail visibly.
            response = await client.get(url + "/messages", auth=(self.identity, self.password), params={"limit": 1})
            response.raise_for_status()
        self.ready = True

    async def send(self, target: str, subject: str, body: dict, *, nonce=None, **correlation):
        if not self.ready:
            await self.register()
        result = await self.client.send(target, payload={"subject": subject, "body": json.dumps(body, ensure_ascii=False), **correlation}, nonce=nonce)
        if result.get("status") != "accepted":
            raise RuntimeError("ATP拒绝消息提交: " + str(result.get("error", "unknown")))
        return result

    async def recv(self):
        if not self.ready:
            await self.register()
        with connect(self.db) as db:
            pending = db.execute("SELECT body FROM inbox WHERE processed=0 ORDER BY rowid LIMIT 50").fetchall()
        if pending:
            return [ATPMessage.from_dict(json.loads(row[0])) for row in pending]
        previous = self.client._last_recv_id
        try:
            messages = await self.client.recv()
            with connect(self.db) as db:
                db.execute("BEGIN IMMEDIATE")
                for message in messages:
                    db.execute("INSERT OR IGNORE INTO inbox(nonce,body) VALUES (?,?)", (message.nonce, message.to_json()))
                db.execute("INSERT OR REPLACE INTO cursor VALUES (1,?)", (self.client._last_recv_id,))
                db.commit()
            return messages
        except BaseException:
            self.client._last_recv_id = previous
            raise

    def acknowledge(self, message):
        # Called only after the consumer's durable business processing succeeds.
        with connect(self.db) as db:
            db.execute("UPDATE inbox SET processed=1 WHERE nonce=?", (message.nonce,))

    async def close(self):
        await self.client.close()
