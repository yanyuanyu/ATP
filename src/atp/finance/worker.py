"""Merchant/payment business agents, deployed in their own ATP network domains.

The durable outbox retains replies when the local server is offline. On restart
ATP may return an old delivery again: incoming nonce deduplication and the
payment ledger's order-level idempotency protect the two separate boundaries.
"""
import asyncio
import json
import os
from pathlib import Path

from atp.finance.ledger import BUYER, CASHIER, MERCHANT, FinanceError, MerchantBook, PaymentLedger, connect
from atp.finance.messaging import Mailbox
from atp.security.sm2 import SM2PrivateKey, SM2PublicKey


class Worker:
    def __init__(self, role, root: Path, private: SM2PrivateKey | None, public: SM2PublicKey):
        self.role, self.root = role, root
        root.mkdir(parents=True, exist_ok=True)
        self.outbox = root / "outbox.db"
        with connect(self.outbox) as db:
            db.execute("CREATE TABLE IF NOT EXISTS replies(nonce TEXT PRIMARY KEY, target TEXT NOT NULL, body TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0)")
        self.book = MerchantBook(root / "merchant.db", private) if role == "merchant" else PaymentLedger(root / "ledger.db", public)

    def process(self, message):
        if message.from_id != BUYER:
            return
        with connect(self.outbox) as db:
            if db.execute("SELECT 1 FROM replies WHERE nonce=?", (message.nonce,)).fetchone():
                return
        try:
            body = json.loads(message.payload["body"])
            if self.role == "merchant" and message.payload.get("subject") == "finance.quote":
                replies = self.book.quote(body, message.from_id)
            elif self.role == "payment" and message.payload.get("subject") == "finance.pay":
                replies = [self.book.pay(body, message.from_id)]
            elif self.role == "payment" and message.payload.get("subject") == "finance.statement":
                replies = [{"kind": "statement", **self.book.snapshot()}]
            else:
                raise FinanceError("INVALID_OPERATION", "该智能体不支持此业务操作")
        except (FinanceError, ValueError, KeyError, TypeError) as exc:
            replies = [{"kind": "business-error", "code": getattr(exc, "code", "INVALID_REQUEST"), "message": str(exc)}]
        with connect(self.outbox) as db:
            db.execute("INSERT OR IGNORE INTO replies(nonce,target,body) VALUES (?,?,?)",
                       (message.nonce, message.from_id, json.dumps({"replies": replies, "task_id": message.payload.get("task_id")})))

    async def flush(self, mailbox):
        with connect(self.outbox) as db:
            rows = db.execute("SELECT * FROM replies WHERE sent=0 ORDER BY rowid LIMIT 20").fetchall()
        for row in rows:
            data = json.loads(row["body"])
            for reply in data["replies"]:
                await mailbox.send(row["target"], "finance.reply", reply, in_reply_to=row["nonce"], task_id=data["task_id"])
            with connect(self.outbox) as db:
                db.execute("UPDATE replies SET sent=1 WHERE nonce=?", (row["nonce"],))


async def main():
    role = os.environ["ATP_FINANCE_ROLE"]
    public = SM2PublicKey(Path(os.environ["ATP_MERCHANT_PUBLIC"]).read_text().strip())
    private = None
    if role == "merchant":
        private = SM2PrivateKey(Path(os.environ["ATP_MERCHANT_PRIVATE"]).read_text().strip(), public.public_key_hex)
    worker = Worker(role, Path(os.environ.get("ATP_FINANCE_DATA", "/finance")), private, public)
    mailbox = Mailbox(MERCHANT if role == "merchant" else CASHIER, os.environ["ATP_FINANCE_SERVER"])
    try:
        while True:
            try:
                for message in await mailbox.recv():
                    await asyncio.to_thread(worker.process, message)
                await worker.flush(mailbox)
            except Exception as exc:
                print(f"finance-{role}: {type(exc).__name__}", flush=True)
                mailbox.ready = False
            await asyncio.sleep(.3)
    finally:
        await mailbox.close()


if __name__ == "__main__":
    asyncio.run(main())
