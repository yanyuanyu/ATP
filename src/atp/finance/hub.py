"""User-side orchestration and evidence store. No fabricated communication events."""
import asyncio
import json
import time
import uuid
from pathlib import Path

from atp.finance.ledger import BUYER, MERCHANT, CASHIER, FinanceError, connect, payment_request, validate_order, verify_quote
from atp.finance.messaging import Mailbox


class Hub:
    def __init__(self, root, public, mailbox):
        self.root, self.public, self.mailbox = Path(root), public, mailbox
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "orders.db"
        self.lock = asyncio.Lock()
        self.connection = {"ready": False, "error": "正在连接用户域 ATP 服务"}
        with connect(self.db) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS requests(nonce TEXT PRIMARY KEY, order_id TEXT, target TEXT, operation TEXT, expected TEXT);
            CREATE TABLE IF NOT EXISTS received(nonce TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS state(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, target TEXT NOT NULL, operation TEXT NOT NULL,
                body TEXT NOT NULL, order_id TEXT, expected TEXT, done INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS attempts(nonce TEXT PRIMARY KEY, operation_id TEXT NOT NULL);
            """)

    def event(self, **data):
        with connect(self.db) as db:
            db.execute("INSERT INTO events(data) VALUES (?)", (json.dumps({"ts": time.time(), **data}, ensure_ascii=False),))

    def order(self, order_id):
        with connect(self.db) as db:
            row = db.execute("SELECT data FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            raise FinanceError("NOT_FOUND", "订单不存在")
        return json.loads(row[0])

    def save(self, data):
        with connect(self.db) as db:
            db.execute("INSERT OR REPLACE INTO orders VALUES (?,?)", (data["order_id"], json.dumps(data, ensure_ascii=False)))

    async def send(self, target, operation, body, order_id=None, expected=None, order_state=None):
        operation_id = uuid.uuid4().hex
        with connect(self.db) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO operations(id,target,operation,body,order_id,expected) VALUES (?,?,?,?,?,?)",
                       (operation_id, target, operation, json.dumps(body), order_id, expected))
            if order_state is not None:
                db.execute("INSERT OR REPLACE INTO orders VALUES (?,?)", (order_id, json.dumps(order_state)))
            db.commit()
        return await self.submit(operation_id)

    async def submit(self, operation_id):
        # Stable business content/order, fresh transport nonce on each attempt:
        # ATP rejects replays of the same local submission nonce.
        nonce = "msg-" + uuid.uuid4().hex
        with connect(self.db) as db:
            db.execute("BEGIN IMMEDIATE")
            op = db.execute("SELECT * FROM operations WHERE id=? AND done=0", (operation_id,)).fetchone()
            if not op:
                return None
            db.execute("INSERT INTO requests VALUES (?,?,?,?,?)", (nonce, op["order_id"], op["target"], op["operation"], op["expected"]))
            db.execute("INSERT INTO attempts VALUES (?,?)", (nonce, operation_id))
            db.execute("UPDATE operations SET attempts=attempts+1,retry_at=? WHERE id=?",
                       (time.time() + min(60, 5 * (op["attempts"] + 1)), operation_id))
            db.commit()
        try:
            await self.mailbox.send(op["target"], op["operation"], json.loads(op["body"]), task_id=op["order_id"], nonce=nonce)
        except Exception as exc:
            self.event(kind="submission_uncertain", nonce=nonce, order_id=op["order_id"],
                       detail="已持久化，等待回执或重试：" + type(exc).__name__)
        else:
            self.event(kind="submitted", sender=BUYER, recipient=op["target"], operation=op["operation"], nonce=nonce,
                       order_id=op["order_id"], detail="已由本域ATP服务接收；等待跨域投递与业务回执")
        return nonce

    async def retry_pending(self):
        with connect(self.db) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM operations WHERE done=0 AND retry_at<=? ORDER BY retry_at LIMIT 10", (time.time(),))]
        for operation_id in ids:
            await self.submit(operation_id)

    async def create(self, quantity):
        order = {"order_id": "JM-" + uuid.uuid4().hex[:16], "sku": "business-stay", "quantity": quantity}
        validate_order(order)
        order.update(status="QUOTING", quotes=[], created_at=time.time())
        await self.send(MERCHANT, "finance.quote", {k: order[k] for k in ("order_id", "sku", "quantity")},
                        order["order_id"], order_state=order)
        return order

    async def pay(self, order_id, variant="pay"):
        order = self.order(order_id)
        if len(order["quotes"]) != 3:
            raise FinanceError("INCOMPLETE_QUOTES", "须先接收三条完整商户签名报价")
        if variant == "pay" and order["status"] in ("PAYMENT_PENDING", "PAID"):
            return order  # Browser retries never create a second financial operation.
        signed = min(order["quotes"], key=lambda x: x["quote"]["total_fen"])
        request = payment_request(signed)
        expected = None
        if variant == "amount":
            request["amount_fen"] += 100
            expected = "PAYMENT_MISMATCH"
        elif variant == "payee":
            request["payee"] = "unexpected-payee"
            expected = "PAYMENT_MISMATCH"
        elif variant == "signature":
            request = json.loads(json.dumps(request))
            request["signed_quote"]["quote"]["total_fen"] += 100
            expected = "INVALID_QUOTE_SIGNATURE"
        elif variant == "unauthorized":
            request["authorized"] = False
            expected = "AUTHORIZATION_REQUIRED"
        elif variant == "repeat":
            if order["status"] != "PAID":
                raise FinanceError("NOT_PAID", "先完成一次模拟支付再验证幂等")
            expected = "IDEMPOTENT"
        elif variant != "pay":
            raise FinanceError("INVALID_OPERATION", "未知验证操作")
        if variant == "pay":
            order.update(status="PAYMENT_PENDING", submitted_at=time.time())
        await self.send(CASHIER, "finance.pay", request, order_id, expected,
                        order_state=order if variant == "pay" else None)
        return order

    async def receive(self, message):
        with connect(self.db) as db:
            if db.execute("SELECT 1 FROM received WHERE nonce=?", (message.nonce,)).fetchone():
                return
            row = db.execute("SELECT * FROM requests WHERE nonce=?", (message.payload.get("in_reply_to"),)).fetchone()
            operation = db.execute("SELECT o.* FROM operations o JOIN attempts a ON a.operation_id=o.id WHERE a.nonce=?",
                                   (message.payload.get("in_reply_to"),)).fetchone()
            if operation and operation["done"]:
                return
        if not row or message.from_id != row["target"] or message.to_id != BUYER or message.payload.get("task_id") != row["order_id"]:
            return
        try:
            body = json.loads(message.payload["body"])
            if not isinstance(body, dict):
                raise ValueError("业务回执须为JSON对象")
            complete = True
            if row["operation"] == "finance.quote":
                quote = verify_quote(body, self.public)
                order = self.order(row["order_id"])
                if (quote["order_id"] != order["order_id"] or quote["sku"] != order["sku"]
                        or quote["quantity"] != order["quantity"] or quote.get("event_index") not in (1,2,3)
                        or quote.get("total_events") != 3):
                    raise FinanceError("QUOTE_MISMATCH", "报价与订单不匹配")
                if not any(x["quote"]["event_index"] == quote["event_index"] for x in order["quotes"]):
                    order["quotes"].append(body)
                if len(order["quotes"]) == 3 and order["status"] == "QUOTING":
                    order["status"] = "AWAITING_AUTHORIZATION"
                self.save(order)
                complete = len(order["quotes"]) == 3
            elif row["operation"] == "finance.pay":
                if body.get("kind") != "payment-result" or body.get("simulation") is not True or body.get("order_id") != row["order_id"]:
                    raise FinanceError("INVALID_RECEIPT", "支付回执未通过关联校验")
                if row["expected"]:
                    passed = ((body.get("approved") is True and body.get("duplicate") is True) if row["expected"] == "IDEMPOTENT"
                              else body.get("approved") is False and body.get("code") == row["expected"])
                    self.event(kind="test", order_id=row["order_id"], test=row["expected"], passed=passed, result=body)
                else:
                    order = self.order(row["order_id"])
                    expected_request = payment_request(min(order["quotes"], key=lambda x: x["quote"]["total_fen"]))
                    if body.get("approved") is True and (not body.get("receipt_id") or any(body.get(k) != expected_request[k] for k in ("payer", "payee", "currency", "amount_fen"))):
                        raise FinanceError("INVALID_RECEIPT", "支付回执金额或参与方不匹配")
                    order.update(status="PAID" if body.get("approved") is True else "REJECTED", receipt=body)
                    self.save(order)
                    await self.send(CASHIER, "finance.statement", {})
            elif row["operation"] == "finance.statement":
                if body.get("kind") != "statement":
                    raise FinanceError("INVALID_STATEMENT", "账本回执无效")
                with connect(self.db) as db:
                    db.execute("INSERT OR REPLACE INTO state VALUES ('statement',?)", (json.dumps(body),))
            with connect(self.db) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT OR IGNORE INTO received VALUES (?)", (message.nonce,))
                if complete and operation:
                    db.execute("UPDATE operations SET done=1 WHERE id=?", (operation["id"],))
                db.commit()
            self.event(kind="received", sender=message.from_id, recipient=BUYER, nonce=message.nonce, order_id=row["order_id"],
                       request_nonce=message.payload.get("in_reply_to"), operation=row["operation"], detail=body)
        except (FinanceError, ValueError, TypeError, KeyError) as exc:
            self.event(kind="invalid_reply", nonce=message.nonce, detail=str(exc))

    def snapshot(self):
        with connect(self.db) as db:
            orders = [json.loads(r[0]) for r in db.execute("SELECT data FROM orders ORDER BY rowid DESC LIMIT 100")]
            events = [json.loads(r[0]) for r in db.execute("SELECT data FROM events ORDER BY id DESC LIMIT 200")]
            row = db.execute("SELECT data FROM state WHERE id='statement'").fetchone()
        return {"connection": self.connection, "orders": orders, "events": events,
                "statement": json.loads(row[0]) if row else None, "simulation": True,
                "transport": "ATP / SM2+SM3+SM4 / TLCP（以实际部署及协议验收为准）"}

    async def loop(self):
        try:
            while True:
                try:
                    async with self.lock:
                        for message in await self.mailbox.recv():
                            await self.receive(message)
                            self.mailbox.acknowledge(message)
                        await self.retry_pending()
                    self.connection = {"ready": True, "error": None}
                except Exception as exc:
                    self.connection = {"ready": False, "error": "用户域通信暂不可用：" + type(exc).__name__}
                    self.mailbox.ready = False
                await asyncio.sleep(.3)
        finally:
            await self.mailbox.close()
