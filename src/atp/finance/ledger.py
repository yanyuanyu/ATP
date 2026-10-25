"""Business rules, separate from ATP's domain authentication and message security.

All amounts are integer CNY fen. This ledger uses test balances only.
Merchant quotes are signed with a dedicated pinned SM2 key; neither the payer
nor the payment service holds the merchant private key.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from atp.security.sm2 import SM2PrivateKey, SM2PublicKey, sm3_digest

BUYER = "buyer@family.test"
MERCHANT = "merchant@hotel.test"
CASHIER = "cashier@payment.test"
PAYEE = "merchant-demo-001"
CATALOG = {"business-stay": {"name": "商旅住宿服务", "prices": [18000, 17000, 19000]}}
TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class FinanceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


@contextmanager
def connect(path: str | Path):
    db = sqlite3.connect(str(path), timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=15000")
    try:
        yield db
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def validate_order(order: dict) -> None:
    if not isinstance(order, dict) or not isinstance(order.get("order_id"), str) or not TOKEN.fullmatch(order["order_id"]):
        raise FinanceError("INVALID_ORDER", "订单编号无效")
    if order.get("sku") not in CATALOG or type(order.get("quantity")) is not int or not 1 <= order["quantity"] <= 7:
        raise FinanceError("INVALID_ORDER", "商品或数量无效，数量须为1至7的整数")


class MerchantBook:
    def __init__(self, path: Path, key: SM2PrivateKey):
        self.path, self.key = path, key
        path.parent.mkdir(parents=True, exist_ok=True)
        with connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS quotes (id TEXT PRIMARY KEY, request TEXT NOT NULL, response TEXT NOT NULL)")

    def quote(self, order: dict, sender: str, now: int | None = None) -> list[dict]:
        validate_order(order)
        if sender != BUYER:
            raise FinanceError("UNAUTHORIZED_PAYER", "此测试商户仅接受已登记用户智能体")
        request = canonical({"buyer": sender, "order": order}).decode()
        now = int(time.time()) if now is None else now
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM quotes WHERE id=?", (order["order_id"],)).fetchone()
            if row:
                if row["request"] != request:
                    raise FinanceError("ORDER_CONFLICT", "同一订单编号不能更改商品或数量")
                return json.loads(row["response"])
            events = []
            for index, price in enumerate(CATALOG[order["sku"]]["prices"], 1):
                body = {**order, "kind": "signed-quote", "quote_id": uuid.uuid4().hex,
                        "merchant": MERCHANT, "buyer": sender, "payee": PAYEE, "currency": "CNY",
                        "unit_price_fen": price, "total_fen": price * order["quantity"],
                        "event_index": index, "total_events": 3, "issued_at": now, "expires_at": now + 900}
                events.append({"quote": body, "signature": self.key.sign(canonical(body)).hex()})
            db.execute("INSERT INTO quotes VALUES (?,?,?)", (order["order_id"], request, json.dumps(events)))
            db.commit()
            return events


def verify_quote(envelope: dict, key: SM2PublicKey) -> dict:
    try:
        quote = envelope["quote"]
        signature = bytes.fromhex(envelope["signature"])
        if not key.verify(signature, canonical(quote)):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise FinanceError("INVALID_QUOTE_SIGNATURE", "商户报价签名无效") from None
    validate_order(quote)
    if (quote.get("kind") != "signed-quote" or quote.get("merchant") != MERCHANT
            or quote.get("buyer") != BUYER or quote.get("payee") != PAYEE or quote.get("currency") != "CNY"
            or type(quote.get("unit_price_fen")) is not int or quote["unit_price_fen"] <= 0
            or type(quote.get("total_fen")) is not int or quote["total_fen"] != quote["unit_price_fen"] * quote["quantity"]
            or type(quote.get("issued_at")) is not int or type(quote.get("expires_at")) is not int
            or quote["expires_at"] <= quote["issued_at"]):
        raise FinanceError("INVALID_QUOTE", "报价字段不满足金融业务规则")
    return quote


class PaymentLedger:
    def __init__(self, path: Path, merchant_key: SM2PublicKey, initial_balance: int = 1000000):
        self.path, self.key = path, merchant_key
        path.parent.mkdir(parents=True, exist_ok=True)
        with connect(path) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, balance_fen INTEGER NOT NULL CHECK(balance_fen>=0));
            CREATE TABLE IF NOT EXISTS payments (order_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, order_id TEXT, outcome TEXT NOT NULL, code TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO accounts VALUES (?,?)", (BUYER, initial_balance))
            db.execute("INSERT OR IGNORE INTO accounts VALUES (?,0)", (PAYEE,))

    def pay(self, request: dict, sender: str, now: int | None = None) -> dict:
        now = int(time.time()) if now is None else now
        order_id = str(request.get("order_id", ""))[:80] if isinstance(request, dict) else ""
        try:
            if sender != BUYER:
                raise FinanceError("UNAUTHORIZED_PAYER", "付款来源与授权用户不符")
            if not isinstance(request, dict):
                raise FinanceError("INVALID_PAYMENT", "付款请求格式无效")
            quote = verify_quote(request.get("signed_quote"), self.key)
            expected = {"order_id": quote["order_id"], "payer": quote["buyer"], "payee": quote["payee"],
                        "currency": quote["currency"], "amount_fen": quote["total_fen"]}
            if request.get("authorized") is not True:
                raise FinanceError("AUTHORIZATION_REQUIRED", "缺少用户模拟支付授权")
            if type(request.get("amount_fen")) is not int or any(request.get(k) != v for k, v in expected.items()):
                raise FinanceError("PAYMENT_MISMATCH", "订单、金额、币种或收款方与商户签名报价不一致")
            fingerprint = sm3_digest(canonical({**expected, "signed_quote": request["signed_quote"]})).hex()
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM payments WHERE order_id=?", (quote["order_id"],)).fetchone()
                if row:
                    if row["fingerprint"] != fingerprint:
                        raise FinanceError("ORDER_CONFLICT", "该订单已按另一份付款请求处理")
                    receipt = json.loads(row["receipt"])
                    receipt["duplicate"] = True
                    db.execute("INSERT INTO audit(ts,order_id,outcome,code) VALUES (?,?,?,?)", (now, quote["order_id"], "reused", "IDEMPOTENT"))
                    db.commit()
                    return receipt
                if not quote["issued_at"] <= now < quote["expires_at"]:
                    raise FinanceError("QUOTE_EXPIRED", "商户报价尚未生效或已过期")
                if quote["total_fen"] > 200000:
                    raise FinanceError("LIMIT_EXCEEDED", "超过单笔模拟支付限额2000元")
                changed = db.execute("UPDATE accounts SET balance_fen=balance_fen-? WHERE id=? AND balance_fen>=?",
                                     (quote["total_fen"], BUYER, quote["total_fen"])).rowcount
                if changed != 1:
                    raise FinanceError("INSUFFICIENT_FUNDS", "模拟账户余额不足")
                db.execute("UPDATE accounts SET balance_fen=balance_fen+? WHERE id=?", (quote["total_fen"], PAYEE))
                receipt = {"kind": "payment-result", "simulation": True, "approved": True, "status": "PAID",
                           **expected, "receipt_id": uuid.uuid4().hex, "paid_at": now, "duplicate": False}
                db.execute("INSERT INTO payments VALUES (?,?,?)", (quote["order_id"], fingerprint, json.dumps(receipt)))
                db.execute("INSERT INTO audit(ts,order_id,outcome,code) VALUES (?,?,?,?)", (now, quote["order_id"], "approved", "PAID"))
                db.commit()
                return receipt
        except FinanceError as exc:
            with connect(self.path) as db:
                db.execute("INSERT INTO audit(ts,order_id,outcome,code) VALUES (?,?,?,?)", (now, order_id, "rejected", exc.code))
            return {"kind": "payment-result", "simulation": True, "approved": False, "status": "REJECTED",
                    "order_id": order_id, "code": exc.code, "message": str(exc)}

    def snapshot(self) -> dict:
        with connect(self.path) as db:
            return {"accounts": [dict(r) for r in db.execute("SELECT * FROM accounts ORDER BY id")],
                    "payments": [json.loads(r[0]) for r in db.execute("SELECT receipt FROM payments ORDER BY rowid DESC")],
                    "audit": [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")]}


def payment_request(envelope: dict) -> dict:
    q = envelope["quote"]
    return {"order_id": q["order_id"], "payer": q["buyer"], "payee": q["payee"], "currency": q["currency"],
            "amount_fen": q["total_fen"], "authorized": True, "signed_quote": envelope}
